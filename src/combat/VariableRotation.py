"""Variable-window rotation for the Augusta / Iuno / ShoreKeeper team.

A thin subclass of :class:`src.combat.StrictRotation.StrictRotation`: it inherits
that class's beat sequence, ordering, flicker debounce and stop-after-opener
bookkeeping unchanged, and overrides only the hand-off. Where the strict rotation
quickswaps off every beat immediately, this one gives each beat a *dwell window*
that runtime conditions can EXTEND.

The strict rotation is a pure quickswap -- ideal when every slot is a fixed
hand-off, but sometimes a character should linger on field longer. Augusta
holding a sub-DPS intro from Iuno wants the full extended-buff window to dump
damage, exactly like the reactive engine's::

    if self.has_sub_dps_intro and self.check_outro() in {'char_iuno'}:
        time_out = 14

That idea is generalised here. Each beat has a :class:`Window` -- a ``base`` dwell
(0 == quickswap, the default) plus a list of :class:`Extension` rules. At run time
the window is ``max(base, seconds of every rule whose predicate fires)``; the
coordinator holds the character on field for that long, doing productive filler
(building concerto on outro beats, spending forte / basics otherwise), before
switching. With every window at 0 it behaves exactly like the strict rotation.

Opt-in via its own config toggle (default OFF). ``get_active_rotation`` returns
this rotation when the toggle is on, else the strict rotation; the three
character classes route ``do_perform`` / ``get_switch_priority`` through it, so
the strict behaviour is unchanged unless the toggle is enabled.

AI editing guide: keep the window predicates SIDE-EFFECT FREE (``compute_window``
may evaluate them more than once and is unit-tested with a fake char), and keep
this module free of heavy imports so the pure logic stays testable.
"""

import time
from collections import namedtuple

try:  # keep importable without the full game stack (tests / tooling)
    from ok import Logger

    logger = Logger.get_logger(__name__)
except Exception:  # pragma: no cover - exercised only when ``ok`` is unavailable
    import logging

    logger = logging.getLogger(__name__)

from src.combat.StrictRotation import (
    StrictRotation, OUTRO_BEAT_TOPOFF_TIME_OUT, OUTRO_TOPOFF_TIME_OUT,
    OUTRO_SWAP_SETTLE,
    build_concerto, confirm_con_full, topoff_concerto, try_spend_forte,
    get_strict_rotation, _combat_control_exceptions,
)

CONFIG_KEY = 'Augusta Iuno SK Variable Rotation'

# A dwell window for one beat.
#   base   : seconds the character holds the field by default (0 = quickswap).
#   extend : list of Extension rules; each that fires raises the window to at
#            least its ``seconds``. The final window is the max of base and every
#            firing rule, so rules never shorten a window, only lengthen it.
Window = namedtuple('Window', ['base', 'extend'])

# One window-extension rule.
#   name      : human-readable id (for logging / tests)
#   predicate : called with the on-field character; truthy -> the rule fires. Must
#               be SIDE-EFFECT FREE (it may be evaluated more than once).
#   seconds   : the window this rule extends the beat to when it fires. Either a
#               number, or a CALLABLE (char -> seconds) for windows RECALCULATED
#               from live state (the BuffTracker's seconds-remaining); evaluated
#               once per fire, so unlike the predicate it may stamp bookkeeping
#               (e.g. receiver binding).
Extension = namedtuple('Extension', ['name', 'predicate', 'seconds'])


def _holds_iuno_sub_dps_intro(char):
    """Augusta just came in on a sub-DPS (Iuno) intro -> hold the field for the
    extended buff window instead of quickswapping. Mirrors the reactive engine's
    ``time_out = 14`` branch; guarded so a char without these hooks (or a
    transient read error) simply does not extend."""
    try:
        return bool(getattr(char, 'has_sub_dps_intro', False)) and \
            char.check_outro() in {'char_iuno'}
    except Exception:
        return False


# The legacy fixed dwell for a visit entered on Iuno's outro amp; also the
# ceiling for the tracker-driven window below and the fallback when the
# tracker is cold.
IUNO_AMP_WINDOW_MAX = 14.0


def _iuno_amp_window(char):
    """Dwell window RECALCULATED from the live remaining on Iuno's outro amp.

    Utilises the BuffTracker instead of a fixed 14: a fresh outro still yields
    ~14s, but a mid-buff re-entry (resync, combat flicker) gets only what is
    genuinely left, so the beat never overstays a dead amplify. Also binds
    ``char`` as the amp's RECEIVER so the tracker expires it the moment she
    swaps out (the kit's early death), keeping remaining() honest for the
    burst gate that reads it next. Falls back to the legacy fixed window when
    the tracker has not seen the buff (cold start / partial wiring)."""
    from src.combat.BuffTracker import get_buff_tracker, IUNO_OUTRO
    tracker = get_buff_tracker(char.task)
    tracker.bind_receiver(IUNO_OUTRO, getattr(char, 'char_name', char.name))
    if not tracker.has(IUNO_OUTRO):
        return IUNO_AMP_WINDOW_MAX
    remaining = tracker.remaining(IUNO_OUTRO)
    window = min(IUNO_AMP_WINDOW_MAX, remaining)
    logger.info(f'VariableRotation: Iuno-amp dwell recalculated from tracker '
                f'remaining {remaining:.1f}s -> window {window:.1f}s')
    return window


# Augusta lingers on field to spend Iuno's transferred amp whenever she enters
# on that sub-DPS intro -- for the amp's LIVE remaining (tracker-driven, capped
# at the legacy 14s); every other beat quickswaps (base 0, no rules) exactly
# like the strict rotation until a window is added here.
_AUGUSTA_IUNO_INTRO = Extension('iuno_sub_dps_intro', _holds_iuno_sub_dps_intro,
                                _iuno_amp_window)

WINDOWS = {
    'aug_burst':  Window(base=0, extend=[_AUGUSTA_IUNO_INTRO]),
    'aug_burst2': Window(base=0, extend=[_AUGUSTA_IUNO_INTRO]),
    'aug_loop1':  Window(base=0, extend=[_AUGUSTA_IUNO_INTRO]),
}

# Default for any beat not in WINDOWS: pure quickswap, no dwell.
_QUICKSWAP = Window(base=0, extend=())


def compute_window(beat, char):
    """Resolve ``beat``'s dwell window (seconds) for the on-field ``char``.

    ``max(base, seconds of every extension rule that fires)``. A beat with no
    window entry (or all predicates false) resolves to its base -- 0 for a
    quickswap. Predicates stay side-effect free; a rule's ``seconds`` may be a
    callable (char -> seconds) recalculated from live state (BuffTracker
    remaining) and is evaluated ONCE per fire. A raising rule (predicate or
    seconds) is skipped -- it must never break the rotation.
    """
    window = WINDOWS.get(beat.name, _QUICKSWAP)
    seconds = window.base
    for ext in window.extend:
        try:
            fired = ext.predicate(char)
        except Exception as e:  # a bad predicate must never break the rotation
            logger.error(f'VariableRotation window rule {ext.name} raised; ignoring: {e}')
            fired = False
        if not fired:
            continue
        try:
            ext_seconds = ext.seconds(char) if callable(ext.seconds) else ext.seconds
        except Exception as e:  # a bad seconds rule must never break the rotation
            logger.error(f'VariableRotation window seconds {ext.name} raised; ignoring: {e}')
            continue
        if ext_seconds > seconds:
            seconds = ext_seconds
    return seconds


class VariableRotation(StrictRotation):
    """Strict ordering + per-beat extendable dwell windows.

    Inherits all of :class:`StrictRotation`'s bookkeeping and overrides only the
    driver to add the windowed dwell. Opt-in (``DEFAULT_ENABLED = False``).
    """

    LABEL = 'VariableRotation'
    CONFIG_KEY = CONFIG_KEY
    DEFAULT_ENABLED = False

    # --- driver ------------------------------------------------------------
    def run_current(self, char):
        """Execute the current beat for ``char``, dwell for its window, then swap.

        Returns True if the beat was handled (caller should return), else False
        to fall back to the character's default reactive rotation.
        """
        if not self.is_active():
            self._diagnose_inactive()
            return False
        self.maybe_reset()
        self._last_seen = time.time()
        beat = self.current_beat()
        if beat.char != char.name:
            if not self.resync(char.name):
                logger.info(f'{self.LABEL} cannot place {char.name}, falling back')
                return False
            beat = self.current_beat()
        window = compute_window(beat, char)
        logger.info(f'{self.LABEL} beat {self.index} {beat.name} ({char.name}) '
                    f'intro={beat.intro} outro={beat.outro} window={window:.1f}s')
        try:
            char.perform_beat(beat)
        except _combat_control_exceptions():
            raise
        except Exception as e:
            # ok-script's Logger has no .exception(); the re-raise carries the trace.
            logger.error(f'{self.LABEL} beat {beat.name} failed; advancing past it: {e}')
            self.advance()
            raise
        # Windowed dwell: hold the field for the (possibly extended) window doing
        # productive filler, then hand off through the shared _handoff (which
        # re-confirms fullness and forces the outro on a full ring). With window
        # == 0 this reduces to the strict quickswap.
        outro_ready = self._dwell(char, beat, window)
        self._handoff(char, beat, outro_ready)
        return True

    def _dwell(self, char, beat, window):
        """Keep ``char`` on field for ``window`` seconds of productive filler,
        then (for outro beats) confirm the concerto ring is full so the swap
        transfers the buff. Returns True when an outro beat is ready to outro.

        window == 0 skips the dwell entirely (quickswap); outro beats still run
        the same bounded top-off the strict rotation uses.
        """
        deadline = time.time() + window
        while time.time() < deadline:
            if beat.outro and char.is_con_full():
                break  # ring already full -> stop early so the outro fires now
            self._dwell_fill(char, beat.outro)
        if beat.outro:
            # MANDATORY top-off (no early-switch bail): the 1st rotation's
            # outro beats ARE its buff hand-offs (user: 'sk should apply outro
            # buff in 1st rotation') -- a miss silently drops the buff the
            # following beats were sequenced around. Per-beat budget (SK's
            # beats get the longer SK_OUTRO_BEAT_TOPOFF_TIME_OUT; the rest the
            # short user-tuned global). Still exits the instant the ring
            # confirms full.
            budget = beat.topoff or OUTRO_BEAT_TOPOFF_TIME_OUT
            ready = (confirm_con_full(char)
                     or topoff_concerto(char, budget,
                                        allow_early_switch=False))
            # log the RAW con next to the decision: a forced outro whose raw read
            # is 0.99 (full only via the angular rescue) is the suspect when the
            # in-game buff does not appear despite con_full=True.
            logger.info(f'{self.LABEL} outro beat {beat.name}: con_full={ready} '
                        f'(raw con={char.get_current_con():.2f})')
            return ready
        return False

    def _dwell_fill(self, char, outro):
        """One productive action during a dwell: build concerto toward an outro,
        otherwise spend a ready forte and keep attacking. Small settle so the
        dwell is not a tight busy-loop."""
        if outro:
            build_concerto(char)
        elif not try_spend_forte(char):
            char.task.click()
        char.sleep(0.05)


def get_variable_rotation(task):
    """Return the task's :class:`VariableRotation`, creating it on first use."""
    rot = getattr(task, '_variable_rotation', None)
    if rot is None:
        rot = VariableRotation(task)
        try:
            task._variable_rotation = rot
        except Exception:  # pragma: no cover - task may forbid attribute set in tests
            pass
    return rot


def get_active_rotation(task):
    """Return the rotation the config selects for this team.

    The variable-window rotation when its toggle is ON, otherwise the strict
    rotation (the default). Both expose the same ``is_active`` / ``priority_for``
    / ``run_current`` interface, so callers stay agnostic -- the three character
    classes route their ``do_perform`` / ``get_switch_priority`` through here.
    """
    var = get_variable_rotation(task)
    if var.config_enabled():
        return var
    return get_strict_rotation(task)


# Reactive-phase outro top-off threshold (mirrors ShoreKeeper's 0.7): finish a
# near-full ring before swapping so the swap outros instead of wasting it.
REACTIVE_TOPOFF_THRESHOLD = 0.7


def reactive_outro_topoff(char, kwargs, threshold=REACTIVE_TOPOFF_THRESHOLD,
                          aggressive=False, mandatory=False):
    """Finish a near-full concerto ring before a REACTIVE-phase swap so it outros.

    Call from a character's ``switch_next_char`` override, passing that call's
    ``kwargs`` (mutated in place): when the ring is in ``[threshold, 1)`` build it
    the rest of the way, then force ``free_intro`` when it is full so the engine
    leaves via the outro (buff transfer) instead of a plain swap that wastes a
    near-full ring.

    ``aggressive``: build with the high-yield ``build_concerto`` (liberation /
    echo / skill, bounded) instead of plain basics. Use for a character whose
    OUTRO buff transfer depends on reaching full and whose concerto comes mostly
    from echo/skill rather than basics (Iuno, ShoreKeeper). Plain basics
    (default) suit a main DPS (Augusta) that must not burn its liberation just
    to top off.

    ``mandatory``: this character's outro buff is REQUIRED by the team cycle
    (Augusta may not burst without it) -- the top-off never bails to an early
    swap and gets a longer build budget.

    Gated on the scripted rotation being INACTIVE: while the coordinator drives
    (the opener) it already tops off before its own hand-offs, and a top-off here
    would double up or outro a non-outro beat. So this only bites in the reactive
    phase that sustains the fight after STOP_AFTER_FIRST_ROTATION hands off.
    """
    if get_active_rotation(char.task).is_active():
        return kwargs
    con = char.get_current_con()
    if threshold <= con < 1:
        if aggressive:
            # Mandatory outros (buffs the team cycle depends on) keep the
            # longer build budget; ordinary near-full finishes use the short
            # user-tuned OUTRO_TOPOFF_TIME_OUT so the reactive flow never
            # stalls long on a ring that refuses to close.
            topoff_concerto(char, 4.0 if mandatory else OUTRO_TOPOFF_TIME_OUT,
                            allow_early_switch=not mandatory)
        else:
            char.continues_normal_attack(0.8, until_con_full=True)
    if confirm_con_full(char):
        # Ground an aerial char before the outro so its buff lands (Iuno is
        # jump-native); wait_down returns at once if she is already grounded.
        if char.flying():
            char.wait_down()
        # settle the last action's animation so the swap is a clean outro (same
        # rationale as the scripted hand-off's OUTRO_SWAP_SETTLE).
        char.sleep(OUTRO_SWAP_SETTLE)
        kwargs['free_intro'] = True
    return kwargs

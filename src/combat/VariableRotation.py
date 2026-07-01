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
    StrictRotation, OUTRO_TOPOFF_TIME_OUT,
    build_concerto, try_spend_forte, get_strict_rotation, _combat_control_exceptions,
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
#   seconds   : the window this rule extends the beat to when it fires.
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


# Augusta lingers ~14s to spend Iuno's transferred buff whenever she enters on
# that sub-DPS intro; every other beat quickswaps (base 0, no rules) exactly like
# the strict rotation until a window is added here.
_AUGUSTA_IUNO_INTRO = Extension('iuno_sub_dps_intro', _holds_iuno_sub_dps_intro, 14)

WINDOWS = {
    'aug_burst':  Window(base=0, extend=[_AUGUSTA_IUNO_INTRO]),
    'aug_burst2': Window(base=0, extend=[_AUGUSTA_IUNO_INTRO]),
    'aug_loop1':  Window(base=0, extend=[_AUGUSTA_IUNO_INTRO]),
}

# Default for any beat not in WINDOWS: pure quickswap, no dwell.
_QUICKSWAP = Window(base=0, extend=())


def compute_window(beat, char):
    """Resolve ``beat``'s dwell window (seconds) for the on-field ``char``.

    Pure function: ``max(base, seconds of every extension rule that fires)``. A
    beat with no window entry (or all predicates false) resolves to its base --
    0 for a quickswap. Kept free of side effects so it is unit-testable with a
    lightweight fake character.
    """
    window = WINDOWS.get(beat.name, _QUICKSWAP)
    seconds = window.base
    for ext in window.extend:
        try:
            fired = ext.predicate(char)
        except Exception:  # a bad predicate must never break the rotation
            logger.exception(f'VariableRotation window rule {ext.name} raised; ignoring')
            fired = False
        if fired and ext.seconds > seconds:
            seconds = ext.seconds
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
        except Exception:
            logger.exception(f'{self.LABEL} beat {beat.name} failed; advancing past it')
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
            return char.is_con_full() or bool(self.task.wait_until(
                char.is_con_full, post_action=lambda: build_concerto(char),
                time_out=OUTRO_TOPOFF_TIME_OUT))
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
                          aggressive=False):
    """Finish a near-full concerto ring before a REACTIVE-phase swap so it outros.

    Call from a character's ``switch_next_char`` override, passing that call's
    ``kwargs`` (mutated in place): when the ring is in ``[threshold, 1)`` build it
    the rest of the way, then force ``free_intro`` when it is full so the engine
    leaves via the outro (buff transfer) instead of a plain swap that wastes a
    near-full ring.

    ``aggressive``: build with the high-yield ``build_concerto`` (liberation /
    echo / skill, bounded) instead of plain basics. Use for a character whose
    OUTRO buff transfer depends on reaching full and whose concerto comes mostly
    from echo/skill rather than basics (Iuno). Plain basics (default) suit a main
    DPS (Augusta) that must not burn its liberation just to top off.

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
            char.task.wait_until(char.is_con_full,
                                 post_action=lambda: build_concerto(char), time_out=2.5)
        else:
            char.continues_normal_attack(0.8, until_con_full=True)
    if char.is_con_full():
        kwargs['free_intro'] = True
    return kwargs

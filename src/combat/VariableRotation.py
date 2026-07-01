"""Variable-window rotation for the Augusta / Iuno / ShoreKeeper team.

This is a sibling of :mod:`src.combat.StrictRotation`. It reuses that module's
beat sequence (``BEATS``) and the per-character ``perform_beat`` implementations
verbatim -- the ORDERING and the scripted key sequences are identical -- and adds
the one thing the strict rotation does not have: a per-beat *dwell window* that
can be EXTENDED at run time by conditions.

The strict rotation is a pure quickswap: every beat fires its sequence and hands
off immediately. That is ideal when every slot is a fixed hand-off, but sometimes
a character should linger on field longer -- e.g. Augusta holding a sub-DPS intro
from Iuno wants the full extended-buff window to dump damage, exactly like the
reactive engine's::

    if self.has_sub_dps_intro and self.check_outro() in {'char_iuno'}:
        time_out = 14

Here that idea is generalised. Each beat has a :class:`Window` -- a ``base`` dwell
(0 == quickswap, the default) plus a list of :class:`Extension` rules. At run time
the window is ``max(base, *seconds of every rule whose predicate fires)``; the
coordinator then holds the character on field for that long, doing productive
filler (building concerto on outro beats, spending forte / basics otherwise),
before switching. With every window left at 0 this module behaves exactly like
the strict rotation.

Opt-in: gated behind its own config key (default OFF). Enabling it swaps the team
from the strict quickswap to this variable-window variant without touching the
strict rotation; ``get_active_rotation`` picks whichever is configured. Everything
degrades to the reactive engine the same way the strict rotation does.

AI editing guide (same as StrictRotation): keep this module free of heavy imports
so the pure ordering/window logic stays unit-testable without the game stack --
talk to characters/task only through duck-typed attributes, and keep the window
predicates side-effect free so ``compute_window`` can be tested with a fake char.
"""

import time
from collections import namedtuple

try:  # keep importable without the full game stack (tests / tooling)
    from ok import Logger

    logger = Logger.get_logger(__name__)
except Exception:  # pragma: no cover - exercised only when ``ok`` is unavailable
    import logging

    logger = logging.getLogger(__name__)

# Reuse the strict rotation's ordering, tokens and shared action helpers so the
# two variants cannot drift apart: this is the SAME rotation, plus windows. (If a
# genuinely different beat sequence is ever wanted, redefine BEATS/LOOP_START
# locally -- the coordinator below only depends on those two names.)
from src.combat.StrictRotation import (
    BEATS, LOOP_START, TEAM, MUST, NO, NORMAL, OUTRO_TOPOFF_TIME_OUT,
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
    extended buff window instead of quickswapping. This mirrors the reactive
    engine's ``time_out = 14`` branch verbatim; guarded so a char without these
    hooks (or a transient read error) simply does not extend."""
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


class VariableRotation:
    """Strict ordering + per-beat extendable dwell windows.

    A copy of :class:`src.combat.StrictRotation.StrictRotation`'s bookkeeping
    (kept standalone so it can evolve independently), with the immediate hand-off
    replaced by a windowed dwell. One instance is attached to the task
    (``task._variable_rotation``) and lives for the whole combat.
    """

    # Same semantics as the strict rotation: run the scripted rotation for the
    # opener (the "1st rotation") then hand the sustained fight to the reactive
    # engine. Reset per new combat.
    STOP_AFTER_FIRST_ROTATION = True

    # A combat-detection flicker (target lock dropping mid-fight) briefly changes
    # task.combat_start; only treat a change as a genuinely NEW combat when there
    # was a real gap since the last beat ran, else the rotation rewinds forever.
    COMBAT_FLICKER_TOLERANCE = 20

    def __init__(self, task):
        self.task = task
        self.index = 0
        self._last_combat_start = None
        self._last_inactive_state = None
        self._last_seen = None
        self._finished = False

    # --- team / enablement -------------------------------------------------
    def team_names(self):
        chars = getattr(self.task, 'chars', None) or []
        return {type(c).__name__ for c in chars if c is not None}

    def team_matches(self):
        return self.team_names() == set(TEAM)

    def config_enabled(self):
        """Opt-in: OFF unless the toggle is explicitly set. Without a config
        object the strict rotation stays the default, so this returns False."""
        char_config = getattr(self.task, 'char_config', None)
        if char_config is None:
            return False
        try:
            return bool(char_config.get(CONFIG_KEY, False))
        except Exception:
            return False

    def is_active(self):
        if self.STOP_AFTER_FIRST_ROTATION and self._finished:
            return False
        return self.config_enabled() and self.team_matches()

    def _diagnose_inactive(self):
        if self.STOP_AFTER_FIRST_ROTATION and self._finished:
            return
        names = self.team_names()
        state = (self.config_enabled(), frozenset(names))
        if state == self._last_inactive_state:
            return
        self._last_inactive_state = state
        if names == set(TEAM) and not self.config_enabled():
            logger.info(
                f"VariableRotation inactive: config '{CONFIG_KEY}' is OFF "
                f"(the strict rotation / reactive engine is used instead)")
        elif names != set(TEAM):
            logger.info(
                f"VariableRotation inactive: on-field team {sorted(names)} != "
                f"required {sorted(TEAM)}")

    # --- beat bookkeeping --------------------------------------------------
    def maybe_reset(self):
        combat_start = getattr(self.task, 'combat_start', None)
        if combat_start == self._last_combat_start:
            return
        self._last_combat_start = combat_start
        brief = (self._last_seen is not None
                 and time.time() - self._last_seen < self.COMBAT_FLICKER_TOLERANCE)
        if brief:
            logger.info('VariableRotation: brief combat re-entry, keeping position')
        else:
            self.index = 0
            self._finished = False
            logger.info('VariableRotation reset to opener for new combat')

    def current_beat(self):
        return BEATS[self.index]

    def advance(self):
        if self.STOP_AFTER_FIRST_ROTATION and self.index == LOOP_START - 1:
            self._finished = True
            logger.info('VariableRotation: 1st rotation (opener) complete -- '
                        'turning off, reactive engine takes over')
        self.index += 1
        if self.index >= len(BEATS):
            self.index = LOOP_START
        return self.current_beat()

    def resync(self, char_name):
        for offset in range(len(BEATS)):
            idx = self.index + offset
            if idx >= len(BEATS):
                idx = LOOP_START + ((idx - len(BEATS)) % (len(BEATS) - LOOP_START))
            if BEATS[idx].char == char_name:
                if idx != self.index:
                    logger.warning(f'VariableRotation resync {self.index} -> {idx} '
                                   f'for {char_name} (skipped {idx - self.index} beat(s))')
                self.index = idx
                return True
        return False

    # --- ordering ----------------------------------------------------------
    def priority_for(self, char_name):
        if not self.is_active():
            return NORMAL
        return MUST if self.current_beat().char == char_name else NO

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
                logger.info(f'VariableRotation cannot place {char.name}, falling back')
                return False
            beat = self.current_beat()
        window = compute_window(beat, char)
        logger.info(f'VariableRotation beat {self.index} {beat.name} ({char.name}) '
                    f'intro={beat.intro} outro={beat.outro} window={window:.1f}s')
        try:
            char.perform_beat(beat)
        except _combat_control_exceptions():
            raise
        except Exception:
            logger.exception(f'VariableRotation beat {beat.name} failed; advancing past it')
            self.advance()
            raise
        # Windowed dwell: hold the field for the (possibly extended) window doing
        # productive filler, then hand off. With window == 0 this reduces to the
        # strict quickswap (outro beats still get the bounded top-off below).
        outro_ready = self._dwell(char, beat, window)
        self.advance()
        char.switch_next_char(free_intro=outro_ready)
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

"""Strict, frame-checked rotation coordinator for the Augusta / Iuno / ShoreKeeper team.

The default combat engine (``BaseCombatTask.switch_next_char``) is *reactive*:
it picks the next on-field character from role + concerto + buff timers. That
is great for arbitrary teams but it cannot follow a hand-authored rotation that
visits the same character several times with different actions each time.

This module adds an opt-in *scripted* layer on top of the reactive engine for
one specific team. A fixed list of "beats" (``BEATS``) encodes the user's
rotation as ``opener`` (played once) + ``loop`` (repeated). Each beat names the
character that should be on field and whether it is entered via an intro and
left via a concerto outro. The coordinator only enforces *ordering*; the actual
per-beat key sequences live in each character's ``perform_beat`` so they can use
that character's own frame-checked helpers (``click_liberation``,
``perform_majesty``, ``do_everything`` ...).

AI editing guide:
- This file is intentionally free of heavy imports (no ``cv2`` / ``ok`` at module
  load) so the pure ordering logic stays unit-testable without the game stack.
  Keep it that way -- talk to characters/task only through duck-typed attributes.
- Ordering is driven through ``priority_for`` which the three character classes
  translate into ``SwitchPriority`` inside their ``get_switch_priority`` override.
- Everything degrades gracefully: if the live team is not the target trio, if the
  config toggle is off, or if the script desyncs from what is actually on screen,
  the characters fall back to their original reactive ``do_perform``.
"""

import time
from collections import namedtuple

try:  # keep this module importable without the full game stack (tests / tooling)
    from ok import Logger

    logger = Logger.get_logger(__name__)
except Exception:  # pragma: no cover - exercised only when ``ok`` is unavailable
    import logging

    logger = logging.getLogger(__name__)

# Priority tokens returned by the coordinator. The character classes map these to
# ``src.char.BaseChar.SwitchPriority`` so this module need not import it.
MUST = 'must'
NO = 'no'
NORMAL = 'normal'

CONFIG_KEY = 'Augusta Iuno SK Strict Rotation'

# Class names of the team this rotation is written for.
TEAM = frozenset({'Augusta', 'Iuno', 'ShoreKeeper'})

# A single step of the rotation.
#   name  : unique id, dispatched on by ``<Char>.perform_beat``
#   char  : class name of the character that must be on field for this beat
#   intro : True when this beat is entered through an intro (previous beat outro'd)
#   outro : True when this beat builds concerto to full and leaves via an outro
#   topoff: per-beat outro top-off budget in seconds; None uses the global
#           OUTRO_BEAT_TOPOFF_TIME_OUT (see there for why SK's beats override it)
Beat = namedtuple('Beat', ['name', 'char', 'intro', 'outro', 'topoff'],
                  defaults=(None,))

# The rotation, transcribed from the user's step list.
#
#   Opener (played once):
#     1  Aug  skill
#     2  Iuno skill
#     3  Sk   ba123 lib ba12 ha skill
#     4  Iuno skill
#     5  Aug  ha
#     6  Iuno echo
#     7  Sk   ba12345 ha outro
#     8  Iuno intro, jump-cancel, lib, skill, ba1234, skill, ba, ha, outro
#     9  Aug  intro, ha, lib (griffin), skill, ha, 2nd lib, [ba123 ha], echo, outro
#    10  Sk   super intro, build concerto, outro
#
#   Loop (repeats):
#    11  Aug  intro, ha
#    12  Iuno skill, echo, dash, skill
#    13  Aug  skill, ha
#    14  Iuno jump, lib, skill, ba1234, skill, ba, ha, outro
#    15  Aug  ha, lib (griffin), skill, ha, 2nd lib, ba123, ha, echo, outro
#    16  Sk   super intro, build concerto, outro  -> back to 11
#
# ShoreKeeper's outro beats get a longer, dedicated top-off budget than the
# (user-tuned, short) global OUTRO_BEAT_TOPOFF_TIME_OUT: her outro buff is what
# the 1st rotation's following beats are sequenced around, and closing her ring
# takes a few seconds of echo/skill build (user: 'in the 1st rotation sk doesnt
# apply outro buff' after tuning the global budget down to 1.0).
SK_OUTRO_BEAT_TOPOFF_TIME_OUT = 6.0

# ``intro`` of beat N always equals ``outro`` of beat N-1 (with loop wraparound),
# i.e. an outro on one beat hands the next beat its intro.
BEATS = [
    # opener
    Beat('aug_open',    'Augusta',     intro=False, outro=False),
    Beat('iuno_open1',  'Iuno',        intro=False, outro=False),
    Beat('sk_open',     'ShoreKeeper', intro=False, outro=False),
    Beat('iuno_open2',  'Iuno',        intro=False, outro=False),
    Beat('aug_open2',   'Augusta',     intro=False, outro=False),
    Beat('iuno_open3',  'Iuno',        intro=False, outro=False),
    Beat('sk_open2',    'ShoreKeeper', intro=False, outro=True,
         topoff=SK_OUTRO_BEAT_TOPOFF_TIME_OUT),
    Beat('iuno_burst',  'Iuno',        intro=True,  outro=True),
    Beat('aug_burst',   'Augusta',     intro=True,  outro=True),
    Beat('sk_intro',    'ShoreKeeper', intro=True,  outro=True,
         topoff=SK_OUTRO_BEAT_TOPOFF_TIME_OUT),
    # loop
    Beat('aug_loop1',   'Augusta',     intro=True,  outro=False),
    Beat('iuno_loop1',  'Iuno',        intro=False, outro=False),
    Beat('aug_loop2',   'Augusta',     intro=False, outro=False),
    Beat('iuno_burst2', 'Iuno',        intro=False, outro=True),
    Beat('aug_burst2',  'Augusta',     intro=True,  outro=True),
    Beat('sk_loop',     'ShoreKeeper', intro=True,  outro=True,
         topoff=SK_OUTRO_BEAT_TOPOFF_TIME_OUT),
]

# Index of the first loop beat; ``advance`` wraps here instead of to 0 so the
# opener is never replayed mid-combat.
LOOP_START = 10

# Reactive-phase (post-opener) outro top-off budget: finish a near-full ring
# before a swap so it outros. Kept SHORT (user-tuned: 'OUTRO_TOPOFF_TIME_OUT =
# 1.0 ... seems to do the rotation well after 1st rotation') -- a long grind
# here stalls the reactive flow; buff-critical outros get the longer mandatory
# budget instead (see reactive_outro_topoff in VariableRotation).
OUTRO_TOPOFF_TIME_OUT = 1.0
# Scripted OUTRO beats top off with this budget and NEVER bail to an early
# switch. Also user-tuned to 1.0: Iuno's and Augusta's opener beats end near
# full anyway, so a longer budget only stalled the opener. The exception is
# ShoreKeeper -- her outro beats carry the buffs the following beats are
# sequenced around, and she needs a few seconds of echo/skill build to close
# the ring (user: 'in the 1st rotation sk doesnt apply outro buff'), so her
# beats override per-beat with SK_OUTRO_BEAT_TOPOFF_TIME_OUT. Every top-off
# still exits the instant the ring confirms full; the budget only caps the
# worst case.
OUTRO_BEAT_TOPOFF_TIME_OUT = 1.0

# Aggressive animation cancel: jump-cancel the last action's recovery on NON-outro
# hand-offs so the swap is immediate. Outro hand-offs never cancel -- they ground
# the char instead so the outro buff lands. User-toggleable via the Character
# Config tab; this is the default when the toggle is absent. (Scoped to the
# leaving char at a swap on purpose -- cancelling around a character's own skills
# would put an aerial char like Iuno airborne and drop her skill buffs.)
AGGRESSIVE_CANCEL_CONFIG_KEY = 'Augusta Iuno SK Aggressive Cancel'
AGGRESSIVE_CANCEL_DEFAULT = True

# While building concerto, hand off as soon as a swap is possible (a target is off
# its switch CD) even below full -- rather than holding the character to reach 100%
# for the outro. Default on; turn off to always build to full for the outro buff.
SWITCH_WHILE_BUILDING_CONFIG_KEY = 'Augusta Iuno SK Switch While Building'
SWITCH_WHILE_BUILDING_DEFAULT = True

# Brief idle before pressing the swap on an OUTRO hand-off. Originally 0.25s to
# let the last animation finish, but the user-verified kit maps say every exit
# action this team uses is SWAP-SAFE: Augusta's False Sovereign echo is meant to
# be swap-cancelled right after cast, Iuno's Absolute Fullness completes and
# transfers its buff even with the outro emitted during it, and all of
# ShoreKeeper's kit (lib field, skill butterflies, Illation) persists off-field.
# Keep only a token input-spacing gap so the swap key never races the last press.
OUTRO_SWAP_SETTLE = 0.1

# Even with switch-while-building ON, never bail out of a concerto top-off when
# the ring is this close to full -- finishing it costs an action or two and wins
# the outro buff, which beats the fraction of a second the early swap would save.
# Evidence (user log): with the bail unconditional, Iuno left at 0.956/0.966 con
# as PLAIN swaps and her outro buffs never reached Augusta.
TOPOFF_HOLD_FROM = 0.9


def _config_flag(task, key, default):
    """Read a boolean Character Config toggle, falling back to ``default`` when the
    config is absent or unreadable."""
    char_config = getattr(task, 'char_config', None)
    if char_config is None:
        return default
    try:
        return bool(char_config.get(key, default))
    except Exception:
        return default


def aggressive_cancel_enabled(task):
    """Whether aggressive animation-cancel is enabled (config toggle, default on)."""
    return _config_flag(task, AGGRESSIVE_CANCEL_CONFIG_KEY, AGGRESSIVE_CANCEL_DEFAULT)


def switch_while_building_enabled(task):
    """Whether to switch out during concerto building as soon as a swap is possible
    (config toggle, default on)."""
    return _config_flag(task, SWITCH_WHILE_BUILDING_CONFIG_KEY, SWITCH_WHILE_BUILDING_DEFAULT)


class StrictRotation:
    """Tracks the current beat and enforces the scripted switch order.

    A single instance is attached to the combat task (``task._strict_rotation``)
    and lives for the whole combat. It is reset whenever a new combat starts.
    """

    # Run the scripted rotation for the OPENER only (the "1st rotation", beats
    # 0..LOOP_START-1), then turn off and hand the sustained fight to the reactive
    # engine. Reset per new combat so the opener runs again each fight.
    STOP_AFTER_FIRST_ROTATION = True

    # Subclass hooks (see VariableRotation): the log label, the config toggle this
    # rotation reads, and whether it is on when the toggle is absent. Kept as class
    # attributes so the shared bookkeeping below needs no per-variant overrides.
    LABEL = 'StrictRotation'
    CONFIG_KEY = CONFIG_KEY
    DEFAULT_ENABLED = True

    def __init__(self, task):
        self.task = task
        self.index = 0
        self._last_combat_start = None
        self._last_inactive_state = None  # dedup for the inactive-reason log
        self._last_seen = None  # wall-clock of the last beat run, for flicker debounce
        self._finished = False  # opener done -> strict rotation off (see STOP_AFTER_FIRST_ROTATION)

    # --- team / enablement -------------------------------------------------
    def team_names(self):
        chars = getattr(self.task, 'chars', None) or []
        return {type(c).__name__ for c in chars if c is not None}

    def team_matches(self):
        return self.team_names() == set(TEAM)

    def config_enabled(self):
        char_config = getattr(self.task, 'char_config', None)
        if char_config is None:
            return self.DEFAULT_ENABLED
        try:
            return bool(char_config.get(self.CONFIG_KEY, self.DEFAULT_ENABLED))
        except Exception:
            return self.DEFAULT_ENABLED

    def is_active(self):
        # Observe new combats HERE, not only in run_current -- the finish-lock
        # bug: once the opener completed (_finished), run_current returned
        # False before its maybe_reset call, so _finished never cleared and no
        # later battle ever got the opener (or the cross-battle state clear)
        # again for the rest of the session (user: 'the rotation becomes
        # unconsistent after the 1st battle. still happening'). is_active is
        # consulted every engine cycle while in combat, so it both notices the
        # new combat_start immediately and doubles as the combat HEARTBEAT the
        # flicker check compares against.
        self.maybe_reset()
        self._last_seen = time.time()
        if self.STOP_AFTER_FIRST_ROTATION and self._finished:
            return False
        return self.config_enabled() and self.team_matches()

    def _diagnose_inactive(self):
        """Log, once per state change, exactly why the strict rotation is off.
        """
        if self.STOP_AFTER_FIRST_ROTATION and self._finished:
            return  # expected: opener done, handed off to the reactive engine
        return self._diagnose_inactive_reason()

    def _diagnose_inactive_reason(self):
        """Report a config/team reason the strict rotation is off.

        Avoids per-frame spam by remembering the last (config, team) state. The
        switch-priority hook printing ``normal`` (instead of ``must``/``no``) is
        the symptom of this; this names the cause.
        """
        names = self.team_names()
        state = (self.config_enabled(), frozenset(names))
        if state == self._last_inactive_state:
            return
        self._last_inactive_state = state
        if names == set(TEAM):
            logger.warning(
                f"{self.LABEL} INACTIVE for team {sorted(names)}: config "
                f"'{self.CONFIG_KEY}' is OFF -- enable it in the Character Config tab to run "
                f"the scripted rotation (otherwise the reactive engine is used)")
        else:
            logger.info(
                f"{self.LABEL} inactive: on-field team {sorted(names)} != "
                f"required {sorted(TEAM)}")

    # --- beat bookkeeping --------------------------------------------------
    # Combat detection flickers mid-fight (the target lock briefly drops during
    # boss animations/movement), which changes task.combat_start and would rewind
    # the whole rotation to the opener each time -- so the rotation never gets past
    # the opener. Only treat a combat_start change as a genuinely NEW combat when
    # there has been a real gap in combat ACTIVITY (_last_seen is stamped on
    # every is_active consult while the engine runs, so it tracks 'last moment
    # in combat', not 'last scripted beat'); a drop-and-reacquire within this
    # many seconds keeps the current rotation position. Real flickers measure
    # 1-5s; distinct battles are >8s apart (kill confirm, loot, travel), so 8
    # separates them where the old 20 misfiled quick back-to-back battles as
    # flickers.
    COMBAT_FLICKER_TOLERANCE = 8

    def maybe_reset(self):
        """Rewind to the opener on a genuinely fresh combat (not a brief flicker)."""
        combat_start = getattr(self.task, 'combat_start', None)
        if combat_start == self._last_combat_start:
            return
        self._last_combat_start = combat_start
        brief = (self._last_seen is not None
                 and time.time() - self._last_seen < self.COMBAT_FLICKER_TOLERANCE)
        if brief:
            logger.info(f'{self.LABEL}: brief combat re-entry, keeping rotation position')
        else:
            self.index = 0
            self._finished = False  # new combat -> run the opener (1st rotation) again
            self._notify_new_combat()
            logger.info(f'{self.LABEL} reset to opener for new combat')

    def _notify_new_combat(self):
        """Clear cross-battle coordination state on a GENUINE new combat.

        None of the tracked in-game buffs survive a battle change (the
        Stellarealm is anchored at the old battle's location, the amps died at
        the last swap, the domain despawned), yet their stamps read up to 40s
        'remaining' into the next battle -- so SK's field-time economy ceded
        visits, Augusta's burst gate thought she was buffed, and the bank
        windows misfired (user: 'the rotation becomes unconsistent after the
        1st battle'). Characters expose an optional duck-typed
        ``on_rotation_new_combat`` for their own volatile windows (SK's outro
        retry claim / forte backoff). Detection FLICKERS never reach here --
        the ``brief`` branch above keeps everything."""
        try:
            from src.combat.BuffTracker import get_buff_tracker
            get_buff_tracker(self.task).clear()
        except Exception as e:  # never break the rotation on cleanup
            logger.debug(f'buff tracker clear on new combat failed: {e}')
        for char in (getattr(self.task, 'chars', None) or []):
            hook = getattr(char, 'on_rotation_new_combat', None)
            if hook is None:
                continue
            try:
                hook()
            except Exception as e:
                logger.debug(f'on_rotation_new_combat failed: {e}')

    def current_beat(self):
        return BEATS[self.index]

    def advance(self):
        # Completing the last opener beat is the end of the "1st rotation" -> turn
        # the scripted rotation off and let the reactive engine sustain the fight.
        if self.STOP_AFTER_FIRST_ROTATION and self.index == LOOP_START - 1:
            self._finished = True
            logger.info(f'{self.LABEL}: 1st rotation (opener) complete -- '
                        f'turning off, reactive engine takes over')
        self.index += 1
        if self.index >= len(BEATS):
            self.index = LOOP_START
        return self.current_beat()

    def resync(self, char_name):
        """Point the script at the next upcoming beat for ``char_name``.

        Used when the on-field character does not match the expected beat (combat
        started on a different character, a switch was missed, etc.). Searches
        forward from the current beat through the loop so recovery prefers the
        nearest future beat. Returns True if a matching beat was found.
        """
        for offset in range(len(BEATS)):
            idx = self.index + offset
            if idx >= len(BEATS):
                idx = LOOP_START + ((idx - len(BEATS)) % (len(BEATS) - LOOP_START))
            if BEATS[idx].char == char_name:
                if idx != self.index:
                    # surfaced at WARNING: a skip means a switch was missed or
                    # combat started off-script, so beats were silently dropped.
                    logger.warning(f'{self.LABEL} resync {self.index} -> {idx} for {char_name} '
                                   f'(skipped {idx - self.index} beat(s))')
                self.index = idx
                return True
        return False

    # --- ordering ----------------------------------------------------------
    def priority_for(self, char_name):
        """Switch priority for ``char_name`` when choosing the next on-field char.

        The coordinator's current beat is the character that should come next, so
        it gets ``MUST`` and the others get ``NO``. Returns ``NORMAL`` when the
        script is inactive so the reactive engine takes over.
        """
        if not self.is_active():
            return NORMAL
        return MUST if self.current_beat().char == char_name else NO

    # --- driver ------------------------------------------------------------
    def run_current(self, char):
        """Execute the current beat for ``char`` and queue the next switch.

        Returns True if the beat was handled (the caller should return), or False
        to fall back to the character's default rotation.
        """
        # Gate first so the coordinator stays inert for non-target teams and when
        # the toggle is off. Log the reason once per state change so it is obvious
        # in the debug log WHY the strict rotation is (not) running.
        if not self.is_active():
            self._diagnose_inactive()
            return False
        self.maybe_reset()
        self._last_seen = time.time()  # mark active-in-combat (for flicker debounce)
        beat = self.current_beat()
        if beat.char != char.name:
            if not self.resync(char.name):
                logger.info(f'{self.LABEL} cannot place {char.name}, falling back')
                return False
            beat = self.current_beat()
        logger.info(f'{self.LABEL} beat {self.index} {beat.name} ({char.name}) '
                    f'intro={beat.intro} outro={beat.outro}')
        try:
            char.perform_beat(beat)
        except _combat_control_exceptions():
            raise  # combat ended / char dead -> let the task loop handle it
        except Exception as e:
            # An unexpected per-beat failure must not pin the rotation on the
            # same beat forever: advance past it, then re-raise so it is visible.
            # ok-script's Logger has no .exception(); the re-raise carries the trace.
            logger.error(f'{self.LABEL} beat {beat.name} failed; advancing past it: {e}')
            self.advance()
            raise
        # Strict sequence: always advance to the next beat (never stay/redo). On
        # an OUTRO beat, top concerto off to full first so the swap fires as a
        # real outro that transfers the buff -- MANDATORY (no early-switch bail,
        # per-beat budget falling back to OUTRO_BEAT_TOPOFF_TIME_OUT): the 1st
        # rotation's buff hand-offs are what the following beats are sequenced
        # around. Exits the instant the ring confirms full; non-outro beats
        # switch immediately.
        outro_ready = False
        if beat.outro:
            budget = beat.topoff or OUTRO_BEAT_TOPOFF_TIME_OUT
            outro_ready = (confirm_con_full(char)
                           or topoff_concerto(char, budget,
                                              allow_early_switch=False))
            logger.info(f'{self.LABEL} outro beat {beat.name}: con_full={outro_ready}')
        self._handoff(char, beat, outro_ready)
        return True

    def _handoff(self, char, beat, outro_ready):
        """Advance the beat and switch out, forcing the outro when the ring is full.

        This is the correct, code-observable substitute for a post-swap "detect a
        missed outro and switch back": that is NOT implementable from this layer.
        Whether the in-game outro physically fired is invisible here --
        ``last_outro_time`` is set unconditionally on the outro *decision*, the
        outgoing char's concerto is zeroed on swap, and any later con read targets
        the *incoming* char's ring. And re-entering ``run_current`` for the same
        char would make ``resync`` silently skip beats. So instead of recovering a
        wasted ring AFTER it is gone, guarantee it PRE-COMMIT: if this is an outro
        beat and the ring is (still) full, leave via the outro path (free_intro)
        so the engine never downgrades a genuinely full ring to a plain swap.

        The extra ``is_con_full`` re-read below catches a ring that settled to full
        on the final top-off action after ``wait_until`` had already returned. It
        is gated on ``beat.outro`` so non-outro beats never poll concerto, and on
        ``not outro_ready`` so a confirmed-full beat is not read twice.
        """
        if beat.outro and not outro_ready and confirm_con_full(char):
            outro_ready = True
            logger.info(f'{self.LABEL} outro beat {beat.name}: ring confirmed full at '
                        f'hand-off, forcing outro')
        if outro_ready:
            # Ground an aerial char before the outro: the engine swap loop never
            # lands her, and an outro fired while airborne/mid-plunge can drop its
            # buff. wait_down returns as soon as she is grounded, so a char already
            # on the ground pays nothing.
            if char.flying():
                logger.info(f'{self.LABEL} grounding {char.name} before outro so its buff lands')
                char.wait_down()
            # ...and let the last action's animation settle so the game takes the
            # swap as a clean outro instead of clipping it mid-recovery.
            char.sleep(OUTRO_SWAP_SETTLE)
        elif aggressive_cancel_enabled(self.task):
            # Aggressive quickswap on non-outro beats: jump-cancel the last action's
            # recovery so the swap is immediate instead of waiting out the animation.
            safe_cancel(char)
        self.advance()
        # free_intro forces the outro path instead of letting switch_next_char
        # re-read the ring -- that second read can flicker to 0.99 and silently
        # downgrade a full ring to a plain swap, dropping the buff transfer. Gated
        # on outro_ready so a not-actually-full ring never fakes an outro.
        char.switch_next_char(free_intro=outro_ready)


def get_strict_rotation(task):
    """Return the task's :class:`StrictRotation`, creating it on first use."""
    rot = getattr(task, '_strict_rotation', None)
    if rot is None:
        rot = StrictRotation(task)
        try:
            task._strict_rotation = rot
        except Exception:  # pragma: no cover - task may forbid attribute set in tests
            pass
    return rot


def _combat_control_exceptions():
    """Combat-flow exceptions that must propagate, not be swallowed as beat errors.

    Imported lazily so this module stays importable without the game stack;
    returns an empty tuple (catches nothing extra) if the import is unavailable.
    """
    try:
        from src.task.BaseCombatTask import NotInCombatException, CharDeadException
        return (NotInCombatException, CharDeadException)
    except Exception:  # pragma: no cover - only when the game stack is absent
        return tuple()


# --- shared frame-checked action helpers ----------------------------------
# Small primitives reused by the per-character ``perform_beat`` implementations.

def safe_cancel(char, settle=0.06):
    """Cancel an action's recovery animation -- safely.

    "Safe" = wait ``settle`` first so the just-performed action's active/damage
    frames have registered (cancelling earlier would drop the hit), THEN jump to
    interrupt the trailing recovery so the next action can start sooner. Jump is
    used as the canceller because it costs no stamina and is the least disruptive
    neutral action; the rotation continues normally afterwards. Do not use this
    right before an outro -- the swap already cancels that recovery for free.
    """
    char.sleep(settle)
    char.task.jump(after_sleep=0.03)


def try_spend_forte(char, check=None):
    """Spend the forte / enhanced heavy the instant its gauge is ready.

    Forte builds continuously in combat; if it is only cashed in at the sparse
    ``heavy()`` beats it OVERCAPS and the surplus is wasted. Call this at more
    points (after basic strings, between casts) so a full gauge is spent
    promptly. ``check`` is the character's own forte detector (defaults to the
    generic ``is_forte_full``; ShoreKeeper uses ``is_mouse_forte_full``, Augusta
    ``check_prowess``). It is a single cheap frame read and the whole call is a
    no-op when the gauge is not ready, so it adds no dead time. Returns True if
    the forte heavy fired.
    """
    check = check or char.is_forte_full
    if check():
        # chars may override HOW the forte is spent (ShoreKeeper holds the
        # click then dodge-cancels the recovery); default is the plain hold.
        spend = getattr(char, 'spend_forte', None)
        if spend is not None:
            return bool(spend(check))
        return bool(char.heavy_click_forte(check))
    return False


def basic_attacks(char, n, interval=0.12, cancel=False, forte_check=None):
    """Send an ``n``-hit basic-attack string (the user's ``ba123`` notation).

    With ``cancel=True`` the basic string's recovery is jump-cancelled once the
    last hit has registered, speeding the transition into the next action.

    ``forte_check`` opts into spending the forte/enhanced heavy AS SOON AS it is
    ready -- polled after every hit instead of only at the next ``heavy()`` beat,
    so a gauge that fills mid-string is cashed in before it overcaps. Pass the
    character's own forte detector; the check is cheap and a no-op when the gauge
    is not ready. Leave it None for characters that RESERVE forte for a special
    action (Iuno's special heavy) so it is not spent out from under them.
    """
    for _ in range(max(0, n)):
        char.task.click()
        char.sleep(interval)
        if forte_check is not None:
            try_spend_forte(char, forte_check)
    if cancel and n > 0:
        safe_cancel(char)


def dash(char):
    """Perform a dodge/dash (the user's ``dash`` step) via the Dodge Key.

    A dodge also cancels recovery, so this doubles as the rotation's dash-cancel.
    """
    key = char.task.key_config.get('Dodge Key', 'lshift')
    char.task.send_key(key, after_sleep=0.05)


def heavy(char, cancel=False):
    """Heavy attack, preferring the forte-charged heavy when it is available.

    With ``cancel=True`` the heavy's (long) recovery is jump-cancelled once the
    hit has registered -- the biggest single time save in the rotation. Leave it
    False for the last heavy before an outro, where the swap cancels it anyway.
    """
    # heavy_click_forte no-ops (returns falsy) when the gauge is not charged, so
    # call it directly instead of pre-reading is_forte_full a second time.
    if char.heavy_click_forte(char.is_forte_full):
        if cancel:
            safe_cancel(char)
        return
    char.heavy_attack()
    if cancel:
        safe_cancel(char)


def build_concerto(char):
    """Outro top-off action: do something that actually builds concerto.

    Basic attacks generate concerto slowly, so polling with plain clicks leaves a
    character a few percent short of full at the outro -- and the outro only fires
    at exactly full, so it never transfers the buff. Escalate to the actions that
    generate the bulk of concerto, in descending yield: liberation, then echo,
    then skill, falling back to a basic attack only when nothing else is ready.
    Each is frame-checked/CD-guarded, so as the big sources go on cooldown this
    naturally walks down to the next available one.
    """
    if char.liberation_available() and char.click_liberation(wait_if_cd_ready=0):
        return
    # Forte/enhanced heavy next -- but only for chars that define HOW to spend
    # it (a spend_forte override: ShoreKeeper's held Illation + dodge-cancel).
    # For her it is one of the biggest concerto chunks, and without this a full
    # bar sat wasted through the whole outro top-off (user: the 6s SK top-off
    # 'is preventing sk from holding mouse click when forte is full'). Chars
    # without the override are deliberately skipped: Iuno's forte is reserved
    # for her buffed special heavy.
    if getattr(char, 'spend_forte', None) is not None and try_spend_forte(char):
        return
    if char.echo_available():
        char.click_echo(time_out=0)
        return
    if char.resonance_available():
        char.send_resonance_key(post_sleep=0.1)
        return
    char.click()


def confirm_con_full(char, settle=0.15):
    """Full only if TWO ring reads a beat apart agree.

    Persistent moving VFX can fake a single full read -- Iuno's Full Moon Domain
    sweeps white-cyan arcs (her own ring colour) through the concerto box for
    seconds at a time. The arcs rotate frame to frame while a genuinely full ring
    is static, so requiring a second read after a short settle rejects them at
    the cost of ~0.15s per confirmed outro.
    """
    if not char.is_con_full():
        return False
    char.sleep(settle)
    return bool(char.is_con_full())


def can_switch_now(char):
    """Whether a hand-off is possible right now: another character is a valid
    switch target AND is off its ~1s switch cooldown.

    Lets the concerto top-off bail out early -- while building concerto we switch
    "whenever possible" even below full, finishing the ring on the next visit,
    rather than holding the character to reach 100%. During the SCRIPTED rotation
    the coordinator marks every other char NO until the beat advances, so
    ``_choose_switch_target`` returns the current char and this stays False (the
    opener's outro beats still build to full); it only bites where role-based
    priorities allow a swap -- the reactive phase.
    """
    task = char.task
    try:
        target = task._choose_switch_target(char, has_intro=False)
        if not target or target is char:
            return False
        return not task._target_has_switch_cd(target)
    except Exception:  # unknown target state -> keep building (do not swap blindly)
        return False


def topoff_concerto(char, time_out, checks_per_action=3, allow_early_switch=True):
    """Build concerto to full, re-reading the ring MORE FREQUENTLY than once per
    build action, so the outro fires the instant the ring completes.

    ``task.wait_until`` re-reads its condition only once per ``post_action`` (see
    the framework loop mirrored in ``Zani.wait_until``), so a ring that tops off
    partway through a long build action (an echo or skill animation) is not seen
    until the NEXT action -- overshooting the full moment and, at worst, spending
    another action's worth of time before the swap. Here every build action is
    followed by ``checks_per_action`` quick ring re-reads (advancing a frame each),
    so a completed ring is caught right after it lands. Bounded by ``time_out``;
    returns True once the ring is full.
    """
    start = time.time()
    while time.time() - start < time_out:
        if confirm_con_full(char):
            return True
        # switch "whenever possible": if a target is ready (off its 1s switch CD)
        # hand off now even below full rather than holding out for the outro. In
        # the scripted rotation the coordinator keeps others NO, so this is False
        # until the beat advances (outro beats still build to full). Config-gated
        # (default on) -- turn it off to always build to full for the outro buff.
        # HOLD BAND: never bail when the ring is >= TOPOFF_HOLD_FROM -- that close,
        # finishing the ring (and its outro buff) beats the early swap. Callers
        # whose outro buff is MANDATORY for the team cycle (Iuno/SK feeding
        # Augusta's burst) pass allow_early_switch=False: their top-off never
        # bails at all -- log evidence showed the bail robbing their outros at
        # 0.60-0.90 con every reactive cycle.
        if (allow_early_switch and switch_while_building_enabled(char.task)
                and can_switch_now(char) and char.get_current_con() < TOPOFF_HOLD_FROM):
            return False
        build_concerto(char)                        # one high-yield build action
        for _ in range(max(1, checks_per_action)):  # then poll the ring frequently
            # single-read poll for speed; a hit is then double-checked (stability
            # vs moving VFX) by confirm_con_full before we call it full.
            if char.is_con_full() and confirm_con_full(char):
                return True
            char.task.next_frame()
    return confirm_con_full(char)

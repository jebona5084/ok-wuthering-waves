"""Team buff tracker for the Augusta / Iuno / ShoreKeeper rotation.

One :class:`BuffTracker` per combat task (``get_buff_tracker``). Characters
STAMP a buff at the moment they apply it in game (SK's Liberation / Stellarealm,
SK's outro amp, Iuno's outro Heavy-Attack amp, Iuno's Full Moon Domain,
Augusta's outro amp); consumers ask ``remaining(name)`` for the live
seconds-before-wear-off and recalculate the rotation from it -- e.g. Augusta's
buffed dwell window shrinks to the ACTUAL time left on Iuno's outro buff, and
her Majesty burst gate requires enough remaining on BOTH support buffs for the
payoff to land inside them, instead of the old "was applied recently" windows.

Why this replaces the scattered recency fields (``ShoreKeeper.outrotime``,
``last_outro_time`` + per-consumer ``*_WINDOW`` constants):
- one clock, one duration table, queried as *seconds remaining* -- the number
  the rotation actually wants;
- receiver-bound buffs (the outro amps) can END EARLY when the receiving
  character is switched off the field, which a pure recency window cannot see;
- every consumer reads the same answer, so the burst gate and the dwell window
  can never disagree about whether a buff is live.

Timing is freeze-adjusted THROUGH THE SOURCE CHARACTER: liberation freezes stop
game time while wall time keeps running, and the characters already maintain a
freeze-compensated clock (``time_elapsed_accounting_for_freeze``). Elapsed for a
buff is computed by the char object that stamped it -- the exact pattern the old
``Augusta._sk_outro_elapsed`` used -- with a wall-clock fallback when the source
is absent (unit tests) or the read raises mid-swap.

AI editing guide: keep this module PURE (no ``cv2`` / game-stack imports at
module load, mirroring StrictRotation/VariableRotation) so the countdown logic
stays unit-testable with fake characters. Talk to characters only through
duck-typed attributes.
"""

import time

try:  # keep importable without the full game stack (tests / tooling)
    from ok import Logger

    logger = Logger.get_logger(__name__)
except Exception:  # pragma: no cover - exercised only when ``ok`` is unavailable
    import logging

    logger = logging.getLogger(__name__)


# --- buff ids ---------------------------------------------------------------
SK_LIBERATION = 'sk_liberation'   # Stellarealm field (persists off-field)
SK_OUTRO = 'sk_outro'             # 15% team amp + recovery butterflies (survives switching)
IUNO_OUTRO = 'iuno_outro'         # 50% Heavy-Attack amp on the incoming char
IUNO_DOMAIN = 'iuno_domain'       # Full Moon Domain (fixed timer, persists off-field)
AUGUSTA_OUTRO = 'augusta_outro'   # 15% amp on receiver; +1 Majesty when the receiver outros

# Default durations in GAME seconds (the freeze-adjusted clock).
# The tuned values from the old recency windows are carried over on purpose so
# swapping the mechanism does not silently retune the rotation:
# - SK buffs are 40.0: the user's ShoreKeeper is S1, which extends her buff
#   duration to 40s (base Stellarealm is a 30s field; the old 29s SK_OUTRO
#   window was tuned against footage before accounting for S1 and discarded
#   still-buffed bursts).
# - IUNO_OUTRO 15.0 keeps IUNO_BUFF_WINDOW (kit says 14s; +1s margin because the
#   buff outlasts the engine's 14s field window a little). Its real early end --
#   the receiver being switched off -- is now modelled by the receiver binding
#   below instead of guessed by the window.
# - IUNO_DOMAIN is a fixed 30s field timer per the kit map (persists with the
#   caster off-field).
DURATIONS = {
    SK_LIBERATION: 40.0,
    SK_OUTRO: 40.0,
    IUNO_OUTRO: 15.0,
    IUNO_DOMAIN: 30.0,
    AUGUSTA_OUTRO: 14.0,
}

# Buffs that ride on a RECEIVING character and end early if that character is
# switched off the field (kit: "this effect ends early if they are switched off
# the field"). The receiver registers itself via ``bind_receiver`` when it
# recognises the intro; ``on_char_switch_out`` then expires the buff the moment
# that character leaves.
RECEIVER_BOUND = frozenset({IUNO_OUTRO, AUGUSTA_OUTRO})


class _Buff:
    __slots__ = ('name', 'source', 'applied_at', 'duration', 'receiver', 'expired')

    def __init__(self, name, source, applied_at, duration):
        self.name = name
        self.source = source          # char object that stamped it (its clock is used)
        self.applied_at = applied_at  # wall-clock stamp; elapsed is freeze-adjusted
        self.duration = duration
        self.receiver = None          # char_name carrying a receiver-bound buff
        self.expired = False          # explicit early end (receiver swapped off)

    def elapsed(self):
        """Freeze-adjusted seconds since application, via the source's clock.

        Mirrors the old cross-char reads (``char.time_elapsed_accounting_for_freeze``
        called on the stamping char). Falls back to wall time when there is no
        source or the read raises -- a transient frame error must never break a
        swap decision.
        """
        fn = getattr(self.source, 'time_elapsed_accounting_for_freeze', None)
        if fn is not None:
            try:
                return float(fn(self.applied_at))
            except Exception:
                pass
        return time.time() - self.applied_at

    def remaining(self):
        if self.expired:
            return 0.0
        return max(0.0, self.duration - self.elapsed())


class BuffTracker:
    """Registry of live team buffs, queried as seconds-remaining."""

    def __init__(self):
        self._buffs = {}

    # --- stamping ------------------------------------------------------
    def apply(self, name, source=None, duration=None):
        """Stamp ``name`` as (re)applied NOW; returns the duration used.

        Re-applying refreshes the timer (matching the in-game behaviour of every
        buff tracked here). ``duration`` overrides the table for odd cases
        (sequences that extend a field, etc.).
        """
        dur = float(duration if duration is not None else DURATIONS.get(name, 0.0))
        self._buffs[name] = _Buff(name, source, time.time(), dur)
        logger.info(f'BuffTracker: applied {name} for {dur:.0f}s')
        return dur

    def bind_receiver(self, name, receiver_char_name):
        """Mark ``receiver_char_name`` as carrying receiver-bound buff ``name``.

        Called by the RECEIVER when it recognises the intro (e.g. Augusta seeing
        ``check_outro() == 'char_iuno'``). No-op if the buff is not currently
        tracked. Returns True when the binding took.
        """
        buff = self._buffs.get(name)
        if buff is None or buff.expired:
            return False
        buff.receiver = receiver_char_name
        logger.debug(f'BuffTracker: {name} receiver bound to {receiver_char_name}')
        return True

    def on_char_switch_out(self, char_name):
        """Expire every receiver-bound buff riding on ``char_name``.

        Call from a character's ``switch_next_char`` at the actual swap: the
        outro amps end the moment their carrier leaves the field, outro or not.
        Returns the list of buff names expired.
        """
        ended = []
        for buff in self._buffs.values():
            if buff.receiver == char_name and not buff.expired and buff.remaining() > 0:
                buff.expired = True
                ended.append(buff.name)
        if ended:
            logger.info(f'BuffTracker: {char_name} switched out -> expired {ended}')
        return ended

    def expire(self, name):
        """Explicitly end ``name`` now (kept for completeness / tests)."""
        buff = self._buffs.get(name)
        if buff is None or buff.expired:
            return False
        buff.expired = True
        return True

    # --- queries ---------------------------------------------------------
    def has(self, name):
        """Whether ``name`` was EVER stamped on this tracker (even if expired).

        Consumers use this to pick tracker-vs-legacy: once the stamping paths
        have fired at least once, the tracker is the authority; before that the
        old recency fields answer (cold-start / partial-wiring safety).
        """
        return name in self._buffs

    def remaining(self, name):
        """Seconds before ``name`` wears off; 0.0 when absent or expired."""
        buff = self._buffs.get(name)
        return buff.remaining() if buff is not None else 0.0

    def is_active(self, name, min_remaining=0.0):
        """Whether ``name`` is live with at least ``min_remaining`` seconds left."""
        return self.remaining(name) > float(min_remaining)

    def snapshot(self):
        """``{name: remaining}`` for every LIVE buff, rounded for logs."""
        out = {}
        for name, buff in self._buffs.items():
            rem = buff.remaining()
            if rem > 0:
                out[name] = round(rem, 1)
        return out


def get_buff_tracker(task):
    """Return the task's :class:`BuffTracker`, creating it on first use.

    Buffs are NOT reset between combats on purpose: remaining() decays to zero
    on its own, and the field buffs (Stellarealm, Full Moon Domain) genuinely
    persist across the brief target-lock flickers that restart combat detection.
    """
    tracker = getattr(task, '_buff_tracker', None)
    if tracker is None:
        tracker = BuffTracker()
        try:
            task._buff_tracker = tracker
        except Exception:  # pragma: no cover - task may forbid attribute set in tests
            pass
    return tracker

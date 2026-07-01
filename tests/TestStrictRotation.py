"""Unit tests for the Augusta/Iuno/ShoreKeeper strict rotation coordinator.

These tests exercise only ``src.combat.StrictRotation`` with lightweight fakes,
so they run without the game stack (no cv2/ok/Qt) and stay fast in CI. The
per-character key sequences in ``perform_beat`` need the live game and are not
covered here; this protects the *ordering* contract that makes the rotation
strict.
"""
import unittest

from src.combat.StrictRotation import (
    StrictRotation, BEATS, LOOP_START, TEAM, MUST, NO, NORMAL, get_strict_rotation,
    try_spend_forte, basic_attacks,
)


def make_char(cls_name):
    """A minimal stand-in whose ``type(...).__name__`` is ``cls_name``."""
    cls = type(cls_name, (object,), {})
    obj = cls()
    obj.name = cls_name
    # defaults so the hand-off's quickswap-cancel / grounding can run in tests
    obj.sleep = lambda *a, **k: None
    obj.flying = lambda: False
    obj.wait_down = lambda *a, **k: None
    return obj


class FakeTask:
    def __init__(self, chars, combat_start=0, char_config=None):
        self.chars = chars
        self.combat_start = combat_start
        self.char_config = {} if char_config is None else char_config
        self.wait_until_calls = []
        for c in chars:
            if c is not None:
                c.task = self  # so safe_cancel(char) can reach char.task.jump

    def wait_until(self, condition, post_action=None, time_out=None):
        self.wait_until_calls.append(time_out)
        return condition()

    def jump(self, *a, **k):
        pass


def team(*names):
    return [make_char(n) for n in names]


def target_team():
    return team('Augusta', 'Iuno', 'ShoreKeeper')


EXPECTED_OPENER = [
    'Augusta', 'Iuno', 'ShoreKeeper', 'Iuno', 'Augusta', 'Iuno',
    'ShoreKeeper', 'Iuno', 'Augusta', 'ShoreKeeper',
]
EXPECTED_LOOP = ['Augusta', 'Iuno', 'Augusta', 'Iuno', 'Augusta', 'ShoreKeeper']


class TestStrictRotation(unittest.TestCase):

    def test_beat_table_consistency(self):
        n = len(BEATS)
        self.assertFalse(BEATS[0].intro, 'opener must not start on an intro')
        for i in range(1, n):
            self.assertEqual(BEATS[i].intro, BEATS[i - 1].outro,
                             f'beat {i} {BEATS[i].name} intro != prev outro')
        self.assertEqual(BEATS[LOOP_START].intro, BEATS[n - 1].outro,
                         'loop wrap intro/outro mismatch')

    def test_no_consecutive_same_char(self):
        for i in range(1, len(BEATS)):
            self.assertNotEqual(BEATS[i].char, BEATS[i - 1].char, f'consecutive char at {i}')
        self.assertNotEqual(BEATS[-1].char, BEATS[LOOP_START].char, 'loop wrap repeats char')

    def test_beats_only_use_team_members(self):
        for beat in BEATS:
            self.assertIn(beat.char, TEAM)

    def test_full_order_opener_then_three_loops(self):
        rot = StrictRotation(FakeTask(target_team()))
        expected = EXPECTED_OPENER + EXPECTED_LOOP * 3
        order = []
        for _ in range(len(expected)):
            order.append(rot.current_beat().char)
            rot.advance()
        self.assertEqual(order, expected)

    def test_advance_wraps_to_loop_not_zero(self):
        rot = StrictRotation(FakeTask(target_team()))
        rot.index = len(BEATS) - 1
        rot.advance()
        self.assertEqual(rot.index, LOOP_START)
        for _ in range(200):
            rot.advance()
            self.assertGreaterEqual(rot.index, LOOP_START)

    def test_priority_must_for_current_no_for_others(self):
        rot = StrictRotation(FakeTask(target_team()))
        rot.index = 0  # aug_open
        self.assertEqual(rot.priority_for('Augusta'), MUST)
        self.assertEqual(rot.priority_for('Iuno'), NO)
        self.assertEqual(rot.priority_for('ShoreKeeper'), NO)
        rot.advance()  # iuno_open1
        self.assertEqual(rot.priority_for('Iuno'), MUST)
        self.assertEqual(rot.priority_for('Augusta'), NO)

    def test_inactive_when_team_mismatch(self):
        rot = StrictRotation(FakeTask(team('Augusta', 'Iuno', 'Verina')))
        self.assertFalse(rot.team_matches())
        self.assertFalse(rot.is_active())
        self.assertEqual(rot.priority_for('Augusta'), NORMAL)

    def test_inactive_with_partial_team(self):
        rot = StrictRotation(FakeTask(team('Augusta', 'Iuno')))
        self.assertFalse(rot.is_active())

    def test_inactive_when_config_off(self):
        rot = StrictRotation(FakeTask(
            target_team(), char_config={'Augusta Iuno SK Strict Rotation': False}))
        self.assertTrue(rot.team_matches())
        self.assertFalse(rot.config_enabled())
        self.assertFalse(rot.is_active())
        self.assertEqual(rot.priority_for('Augusta'), NORMAL)

    def test_active_when_config_missing_defaults_on(self):
        rot = StrictRotation(FakeTask(target_team(), char_config={}))
        self.assertTrue(rot.is_active())

    def test_resync_finds_nearest_future_beat(self):
        rot = StrictRotation(FakeTask(target_team()))
        rot.index = 0  # expects Augusta, but ShoreKeeper is on field
        self.assertTrue(rot.resync('ShoreKeeper'))
        self.assertEqual(rot.current_beat().char, 'ShoreKeeper')
        self.assertEqual(rot.index, 2)  # sk_open

    def test_resync_wraps_through_loop(self):
        rot = StrictRotation(FakeTask(target_team()))
        rot.index = len(BEATS) - 1  # sk_loop; next Augusta is loop start
        self.assertTrue(rot.resync('Augusta'))
        self.assertEqual(rot.index, LOOP_START)

    def test_maybe_reset_on_new_combat(self):
        task = FakeTask(target_team(), combat_start=100)
        rot = StrictRotation(task)
        rot.maybe_reset()
        rot.index = 12
        rot.maybe_reset()  # same combat -> keep position
        self.assertEqual(rot.index, 12)
        task.combat_start = 200  # new combat -> rewind to opener
        rot.maybe_reset()
        self.assertEqual(rot.index, 0)

    def test_stops_strict_after_opener(self):
        # STOP_AFTER_FIRST_ROTATION: once the opener (beats 0..LOOP_START-1) is
        # done, is_active() goes False so the reactive engine takes over.
        rot = StrictRotation(FakeTask(target_team()))
        self.assertTrue(rot.STOP_AFTER_FIRST_ROTATION)
        self.assertTrue(rot.is_active())
        for _ in range(LOOP_START):  # advance through the opener into the loop
            rot.advance()
        self.assertTrue(rot._finished)
        self.assertFalse(rot.is_active())
        self.assertEqual(rot.priority_for('Augusta'), NORMAL)

    def test_new_combat_reenables_strict_after_finish(self):
        task = FakeTask(target_team(), combat_start=1)
        rot = StrictRotation(task)
        rot.maybe_reset()
        for _ in range(LOOP_START):
            rot.advance()
        self.assertTrue(rot._finished)
        task.combat_start = 2  # genuinely new combat
        rot.maybe_reset()
        self.assertFalse(rot._finished)
        self.assertTrue(rot.is_active())
        self.assertEqual(rot.index, 0)

    def test_maybe_reset_keeps_position_on_brief_flicker(self):
        # A brief combat drop/reacquire (target lock flicker) changes combat_start
        # but must NOT rewind: keep the rotation position when a beat ran recently.
        import time
        task = FakeTask(target_team(), combat_start=100)
        rot = StrictRotation(task)
        rot.maybe_reset()
        rot.index = 13
        rot._last_seen = time.time()  # a beat just ran
        task.combat_start = 200  # detection flickered and re-entered
        rot.maybe_reset()
        self.assertEqual(rot.index, 13)

    def test_maybe_reset_rewinds_after_real_gap(self):
        # A genuine new combat (long gap since the last beat) rewinds to the opener.
        import time
        task = FakeTask(target_team(), combat_start=100)
        rot = StrictRotation(task)
        rot.maybe_reset()
        rot.index = 13
        rot._last_seen = time.time() - (rot.COMBAT_FLICKER_TOLERANCE + 5)
        task.combat_start = 200
        rot.maybe_reset()
        self.assertEqual(rot.index, 0)

    def test_run_current_executes_and_advances(self):
        task = FakeTask(target_team())
        rot = StrictRotation(task)
        aug = task.chars[0]
        calls = []
        aug.perform_beat = lambda beat: calls.append(('perform', beat.name))
        aug.switch_next_char = lambda free_intro=False: calls.append(('switch', free_intro))
        rot.index = 0
        self.assertTrue(rot.run_current(aug))
        self.assertEqual(calls, [('perform', 'aug_open'), ('switch', False)])
        self.assertEqual(rot.index, 1)

    def test_run_current_outro_beat_tops_off_concerto_then_switches(self):
        # On an OUTRO beat run_current briefly tops off concerto (bounded) so the
        # swap fires as an outro, then switches. It still advances (strict).
        task = FakeTask(target_team())
        rot = StrictRotation(task)
        rot.maybe_reset()  # sync combat tracking so run_current won't rewind
        sk = task.chars[2]
        events = []
        sk.perform_beat = lambda beat: events.append(('beat', beat.name))
        sk.is_con_full = lambda: False  # not full -> bounded top-off attempted
        sk.click_with_interval = lambda *a, **k: None
        sk.switch_next_char = lambda *a, **k: events.append(('switch', a, k))
        rot.index = 6  # sk_open2, outro=True
        self.assertTrue(rot.run_current(sk))
        # con never reached full -> plain swap (free_intro=False), no faked outro
        self.assertEqual(events, [('beat', 'sk_open2'), ('switch', (), {'free_intro': False})])
        self.assertEqual(len(task.wait_until_calls), 1)  # bounded top-off ran
        self.assertEqual(rot.index, 7)  # still advanced (strict sequence)

    def test_run_current_outro_beat_no_topoff_when_already_full(self):
        task = FakeTask(target_team())
        rot = StrictRotation(task)
        rot.maybe_reset()
        sk = task.chars[2]
        events = []
        sk.perform_beat = lambda beat: events.append(('beat', beat.name))
        sk.is_con_full = lambda: True  # already full -> no top-off wait
        sk.switch_next_char = lambda *a, **k: events.append(('switch', a, k))
        rot.index = 6  # sk_open2, outro=True
        self.assertTrue(rot.run_current(sk))
        self.assertEqual(task.wait_until_calls, [])  # no wait needed
        # already full -> force the outro path (free_intro=True)
        self.assertEqual(events, [('beat', 'sk_open2'), ('switch', (), {'free_intro': True})])

    def test_run_current_outro_forces_outro_when_ring_settles_full_at_handoff(self):
        # The ring reads not-full during the bounded top-off (wait_until returns
        # False) but is full at the final hand-off re-confirm -> force the outro
        # (free_intro=True) instead of wasting the full ring on a plain swap. This
        # is the pre-commit "use the ring while it is still full" guard; there is
        # deliberately NO post-swap detect-and-switch-back (undetectable + desyncs).
        task = FakeTask(target_team())
        rot = StrictRotation(task)
        rot.maybe_reset()
        sk = task.chars[2]
        events = []
        con_calls = {'n': 0}

        def con_full():
            con_calls['n'] += 1
            return con_calls['n'] >= 3  # False for the 2 top-off reads, True at _handoff

        sk.perform_beat = lambda beat: events.append(('beat', beat.name))
        sk.is_con_full = con_full
        sk.switch_next_char = lambda *a, **k: events.append(('switch', a, k))
        rot.index = 6  # sk_open2, outro=True
        self.assertTrue(rot.run_current(sk))
        self.assertEqual(events, [('beat', 'sk_open2'), ('switch', (), {'free_intro': True})])
        self.assertEqual(rot.index, 7)  # advanced exactly once (no re-entry / switch-back)

    def test_run_current_non_outro_beat_switches_plain(self):
        task = FakeTask(target_team())
        rot = StrictRotation(task)
        rot.maybe_reset()
        aug = task.chars[0]
        events = []
        aug.perform_beat = lambda beat: events.append(('beat', beat.name))
        aug.is_con_full = lambda: self.fail('non-outro beat must not poll concerto')
        aug.switch_next_char = lambda *a, **k: events.append(('switch', a, k))
        rot.index = 0  # aug_open, outro=False
        self.assertTrue(rot.run_current(aug))
        # non-outro -> plain swap, free_intro=False
        self.assertEqual(events, [('beat', 'aug_open'), ('switch', (), {'free_intro': False})])

    def test_run_current_outro_grounds_flying_char_before_swap(self):
        # An aerial char is landed (wait_down) BEFORE the outro swap so the outro
        # buff lands, then swaps via the outro path.
        task = FakeTask(target_team())
        rot = StrictRotation(task)
        rot.maybe_reset()
        sk = task.chars[2]
        events = []
        sk.perform_beat = lambda beat: None
        sk.is_con_full = lambda: True
        sk.flying = lambda: True
        sk.wait_down = lambda *a, **k: events.append('grounded')
        sk.switch_next_char = lambda *a, **k: events.append(('switch', k.get('free_intro')))
        rot.index = 6  # sk_open2, outro=True
        self.assertTrue(rot.run_current(sk))
        self.assertEqual(events, ['grounded', ('switch', True)])  # grounded THEN outro

    def test_run_current_non_outro_jump_cancels_for_quickswap(self):
        # Non-outro hand-off jump-cancels recovery (aggressive quickswap) before
        # the swap; a grounded char is not wait_down'd.
        task = FakeTask(target_team())
        task.jumped = 0
        task.jump = lambda *a, **k: setattr(task, 'jumped', task.jumped + 1)
        rot = StrictRotation(task)
        rot.maybe_reset()
        aug = task.chars[0]
        events = []
        aug.perform_beat = lambda beat: None
        aug.wait_down = lambda *a, **k: events.append('grounded')  # must NOT be called
        aug.switch_next_char = lambda *a, **k: events.append('switch')
        rot.index = 0  # aug_open, outro=False
        self.assertTrue(rot.run_current(aug))
        self.assertEqual(task.jumped, 1)      # jump-cancelled once before the swap
        self.assertEqual(events, ['switch'])  # no grounding on a non-outro beat

    def test_aggressive_cancel_config_off_disables_quickswap_cancel(self):
        task = FakeTask(target_team(),
                        char_config={'Augusta Iuno SK Aggressive Cancel': False})
        task.jumped = 0
        task.jump = lambda *a, **k: setattr(task, 'jumped', task.jumped + 1)
        rot = StrictRotation(task)
        rot.maybe_reset()
        aug = task.chars[0]
        events = []
        aug.perform_beat = lambda beat: None
        aug.switch_next_char = lambda *a, **k: events.append('switch')
        rot.index = 0  # non-outro
        self.assertTrue(rot.run_current(aug))
        self.assertEqual(task.jumped, 0)      # toggle off -> plain swap, no cancel
        self.assertEqual(events, ['switch'])

    def test_run_current_resets_to_opener_on_first_call(self):
        # _last_combat_start starts unset, so the first run_current rewinds to
        # the opener even if index was nudged beforehand.
        task = FakeTask(target_team())
        rot = StrictRotation(task)
        rot.index = 9
        aug = task.chars[0]
        aug.perform_beat = lambda beat: None
        aug.switch_next_char = lambda free_intro=False: None
        rot.run_current(aug)
        self.assertEqual(rot.index, 1)  # ran aug_open (0) then advanced to 1

    def test_run_current_inactive_returns_false(self):
        task = FakeTask(team('Augusta', 'Iuno', 'Verina'))
        rot = StrictRotation(task)
        aug = task.chars[0]
        aug.perform_beat = lambda beat: self.fail('should not run beat when inactive')
        aug.switch_next_char = lambda free_intro=False: None
        self.assertFalse(rot.run_current(aug))

    def test_run_current_inactive_has_no_side_effects(self):
        # When inactive the coordinator must not mutate state or reset (no log
        # spam / index churn for non-target teams).
        task = FakeTask(team('Augusta', 'Iuno', 'Verina'), combat_start=5)
        rot = StrictRotation(task)
        rot.index = 5
        rot._last_combat_start = 'sentinel'
        aug = task.chars[0]
        aug.perform_beat = lambda beat: self.fail('inactive must not run beat')
        self.assertFalse(rot.run_current(aug))
        self.assertEqual(rot.index, 5)
        self.assertEqual(rot._last_combat_start, 'sentinel')

    def test_run_current_advances_past_failing_beat(self):
        # An unexpected per-beat error must advance the index (so the same beat
        # is not retried forever) and propagate.
        task = FakeTask(target_team())
        rot = StrictRotation(task)
        rot.maybe_reset()
        aug = task.chars[0]
        aug.perform_beat = lambda beat: (_ for _ in ()).throw(ValueError('boom'))
        aug.switch_next_char = lambda *a, **k: self.fail('must not switch on failure')
        rot.index = 0
        with self.assertRaises(ValueError):
            rot.run_current(aug)
        self.assertEqual(rot.index, 1)

    def test_get_strict_rotation_is_cached_per_task(self):
        task = FakeTask(target_team())
        first = get_strict_rotation(task)
        second = get_strict_rotation(task)
        self.assertIs(first, second)


class _ForteTask:
    def __init__(self):
        self.clicks = 0

    def click(self):
        self.clicks += 1


class _ForteChar:
    """Minimal char stand-in for the forte-spending helpers: a full gauge is
    drained by one ``heavy_click_forte`` call (as the real hold does)."""

    def __init__(self, forte_full=False):
        self.task = _ForteTask()
        self._full = forte_full
        self.heavy_calls = 0

    def sleep(self, _):
        pass

    def is_forte_full(self):
        return self._full

    def heavy_click_forte(self, check):
        self.heavy_calls += 1
        self._full = False  # spending the forte heavy drains the gauge
        return True


class TestForteSpending(unittest.TestCase):
    """Contract for try_spend_forte / basic_attacks(forte_check=...): spend the
    forte heavy the instant it is ready, and never touch it otherwise."""

    def test_spends_when_full_and_reports_true(self):
        c = _ForteChar(forte_full=True)
        self.assertTrue(try_spend_forte(c))
        self.assertEqual(c.heavy_calls, 1)
        self.assertFalse(c._full)

    def test_noop_when_not_full_and_reports_false(self):
        c = _ForteChar(forte_full=False)
        self.assertFalse(try_spend_forte(c))
        self.assertEqual(c.heavy_calls, 0)

    def test_uses_supplied_detector(self):
        c = _ForteChar(forte_full=False)  # generic detector says empty...
        self.assertTrue(try_spend_forte(c, lambda: True))  # ...but custom says ready
        self.assertEqual(c.heavy_calls, 1)

    def test_basic_attacks_spends_forte_promptly_then_noops(self):
        c = _ForteChar(forte_full=True)
        basic_attacks(c, 3, interval=0, forte_check=c.is_forte_full)
        self.assertEqual(c.task.clicks, 3)
        # full at the first hit -> spent once; empty for the remaining hits
        self.assertEqual(c.heavy_calls, 1)

    def test_basic_attacks_without_forte_check_never_spends(self):
        c = _ForteChar(forte_full=True)
        basic_attacks(c, 3, interval=0)
        self.assertEqual(c.task.clicks, 3)
        self.assertEqual(c.heavy_calls, 0)
        self.assertTrue(c._full)  # forte left untouched (reserved, e.g. Iuno)


if __name__ == '__main__':
    unittest.main()

"""Unit tests for src/combat/BuffTracker.py (pure logic, no game stack).

Run: PYTHONPATH=. python -m unittest tests.TestBuffTracker
"""
import time
import unittest

from src.combat.BuffTracker import (
    BuffTracker, get_buff_tracker, DURATIONS,
    SK_LIBERATION, SK_OUTRO, IUNO_OUTRO, AUGUSTA_OUTRO,
)


class FakeSourceChar:
    """Duck-typed stamping char with a controllable freeze-adjusted clock."""

    def __init__(self):
        self.extra_frozen = 0.0

    def time_elapsed_accounting_for_freeze(self, start):
        return (time.time() - start) - self.extra_frozen


class RaisingSourceChar:
    def time_elapsed_accounting_for_freeze(self, start):
        raise RuntimeError('mid-swap frame error')


class FakeTask:
    pass


class TestBuffTracker(unittest.TestCase):
    def setUp(self):
        self.tracker = BuffTracker()

    def test_apply_and_remaining_decay(self):
        dur = self.tracker.apply(SK_OUTRO)
        self.assertEqual(dur, DURATIONS[SK_OUTRO])
        rem = self.tracker.remaining(SK_OUTRO)
        self.assertGreater(rem, DURATIONS[SK_OUTRO] - 1)
        self.assertLessEqual(rem, DURATIONS[SK_OUTRO])
        self.assertTrue(self.tracker.is_active(SK_OUTRO))
        self.assertTrue(self.tracker.is_active(SK_OUTRO, min_remaining=4.0))

    def test_absent_buff_reads_zero_and_cold(self):
        self.assertEqual(self.tracker.remaining(SK_LIBERATION), 0.0)
        self.assertFalse(self.tracker.is_active(SK_LIBERATION))
        self.assertFalse(self.tracker.has(SK_LIBERATION))

    def test_has_is_sticky_after_expiry(self):
        self.tracker.apply(IUNO_OUTRO)
        self.tracker.expire(IUNO_OUTRO)
        self.assertTrue(self.tracker.has(IUNO_OUTRO))       # tracker stays authority
        self.assertEqual(self.tracker.remaining(IUNO_OUTRO), 0.0)

    def test_duration_override(self):
        self.tracker.apply(SK_LIBERATION, duration=40.0)
        self.assertGreater(self.tracker.remaining(SK_LIBERATION), 39.0)

    def test_reapply_refreshes(self):
        self.tracker.apply(IUNO_OUTRO, duration=0.0)        # instantly worn off
        self.assertFalse(self.tracker.is_active(IUNO_OUTRO))
        self.tracker.apply(IUNO_OUTRO)                      # refresh with table duration
        self.assertGreater(self.tracker.remaining(IUNO_OUTRO), 10.0)

    def test_receiver_bound_expires_on_switch_out(self):
        self.tracker.apply(IUNO_OUTRO)
        self.assertTrue(self.tracker.bind_receiver(IUNO_OUTRO, 'Augusta'))
        ended = self.tracker.on_char_switch_out('Augusta')
        self.assertEqual(ended, [IUNO_OUTRO])
        self.assertEqual(self.tracker.remaining(IUNO_OUTRO), 0.0)

    def test_switch_out_of_other_char_is_noop(self):
        self.tracker.apply(IUNO_OUTRO)
        self.tracker.bind_receiver(IUNO_OUTRO, 'Augusta')
        self.assertEqual(self.tracker.on_char_switch_out('char_iuno'), [])
        self.assertTrue(self.tracker.is_active(IUNO_OUTRO))

    def test_bind_receiver_requires_live_buff(self):
        self.assertFalse(self.tracker.bind_receiver(AUGUSTA_OUTRO, 'ShoreKeeper'))
        self.tracker.apply(AUGUSTA_OUTRO)
        self.tracker.expire(AUGUSTA_OUTRO)
        self.assertFalse(self.tracker.bind_receiver(AUGUSTA_OUTRO, 'ShoreKeeper'))

    def test_unbound_buff_survives_switch_out(self):
        self.tracker.apply(SK_OUTRO)                        # duration-only buff
        self.tracker.on_char_switch_out('Augusta')
        self.assertTrue(self.tracker.is_active(SK_OUTRO))

    def test_freeze_adjusted_elapsed_via_source(self):
        source = FakeSourceChar()
        self.tracker.apply(IUNO_OUTRO, source=source)
        source.extra_frozen = 10.0                          # 10s of liberation freeze
        rem = self.tracker.remaining(IUNO_OUTRO)
        # game clock stood still for 10s -> remaining reads ABOVE the table value
        self.assertGreater(rem, DURATIONS[IUNO_OUTRO] + 9.0)

    def test_source_clock_error_falls_back_to_wall_time(self):
        self.tracker.apply(SK_OUTRO, source=RaisingSourceChar())
        rem = self.tracker.remaining(SK_OUTRO)
        self.assertGreater(rem, DURATIONS[SK_OUTRO] - 1)
        self.assertLessEqual(rem, DURATIONS[SK_OUTRO])

    def test_snapshot_lists_only_live(self):
        self.tracker.apply(SK_OUTRO)
        self.tracker.apply(IUNO_OUTRO)
        self.tracker.expire(IUNO_OUTRO)
        snap = self.tracker.snapshot()
        self.assertIn(SK_OUTRO, snap)
        self.assertNotIn(IUNO_OUTRO, snap)

    def test_get_buff_tracker_memoizes_on_task(self):
        task = FakeTask()
        t1 = get_buff_tracker(task)
        t2 = get_buff_tracker(task)
        self.assertIs(t1, t2)


if __name__ == '__main__':
    unittest.main()

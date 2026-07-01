"""Unit tests for the variable-window rotation.

Like TestStrictRotation these use lightweight fakes and no game stack. They
cover the pieces that differ from the strict rotation: the config opt-in, the
strict/variable dispatch, the pure ``compute_window`` logic, and that a beat
with a zero window still quickswaps exactly like the strict rotation.
"""
import time
import unittest
from unittest.mock import patch

from src.combat.StrictRotation import Beat, StrictRotation
from src.combat.VariableRotation import (
    VariableRotation, Window, Extension, compute_window, CONFIG_KEY,
    get_variable_rotation, get_active_rotation, WINDOWS, reactive_outro_topoff,
)


def make_char(cls_name):
    cls = type(cls_name, (object,), {})
    obj = cls()
    obj.name = cls_name
    return obj


def target_team():
    return [make_char(n) for n in ('Augusta', 'Iuno', 'ShoreKeeper')]


class FakeTask:
    def __init__(self, chars, combat_start=0, char_config=None):
        self.chars = chars
        self.combat_start = combat_start
        self.char_config = {} if char_config is None else char_config
        self.wait_until_calls = []
        self.clicks = 0

    def wait_until(self, condition, post_action=None, time_out=None):
        self.wait_until_calls.append(time_out)
        return condition()

    def click(self):
        self.clicks += 1

    def jump(self, *a, **k):
        pass

    def next_frame(self, *a, **k):
        pass

    # default: no other switch target -> can_switch_now False -> keep building
    def _choose_switch_target(self, char, has_intro=False, target_low_con=False):
        return char

    def _target_has_switch_cd(self, char):
        return False


class FakeChar:
    """Rich stand-in that records perform_beat / switch and supports the dwell
    fill (build_concerto / try_spend_forte fall back to no-ops here)."""

    def __init__(self, name, task, con_full=False, forte_full=False,
                 has_sub_dps_intro=False, outro='char_iuno'):
        self.name = name
        self.task = task
        self._con_full = con_full
        self._forte_full = forte_full
        self.has_sub_dps_intro = has_sub_dps_intro
        self._outro = outro
        self.beats = []
        self.switches = []
        self.fill_clicks = 0

    # --- scripted-beat hooks ---
    def perform_beat(self, beat):
        self.beats.append(beat.name)

    def switch_next_char(self, free_intro=False):
        self.switches.append(free_intro)

    # --- state reads ---
    def is_con_full(self):
        return self._con_full

    def is_forte_full(self):
        return self._forte_full

    def check_outro(self):
        return self._outro

    def flying(self):
        return False

    def wait_down(self, *a, **k):
        pass

    def heavy_click_forte(self, check):
        self._forte_full = False
        return True

    def sleep(self, _):
        pass

    # --- build_concerto deps (all inert so an outro dwell is a no-op) ---
    def liberation_available(self):
        return False

    def echo_available(self):
        return False

    def resonance_available(self):
        return False

    def click_echo(self, time_out=0):
        pass

    def send_resonance_key(self, post_sleep=0):
        pass

    def click_liberation(self, wait_if_cd_ready=0):
        return False

    def click(self):
        self.fill_clicks += 1


ON = {CONFIG_KEY: True}


class TestVariableConfig(unittest.TestCase):

    def test_off_by_default(self):
        rot = VariableRotation(FakeTask(target_team()))
        self.assertFalse(rot.config_enabled())
        self.assertFalse(rot.is_active())

    def test_on_when_toggled(self):
        rot = VariableRotation(FakeTask(target_team(), char_config=dict(ON)))
        self.assertTrue(rot.config_enabled())
        self.assertTrue(rot.is_active())

    def test_inactive_when_team_mismatch_even_if_on(self):
        chars = [make_char('Augusta'), make_char('Iuno'), make_char('Verina')]
        rot = VariableRotation(FakeTask(chars, char_config=dict(ON)))
        self.assertFalse(rot.is_active())


class TestActiveRotationDispatch(unittest.TestCase):

    def test_returns_strict_when_variable_off(self):
        task = FakeTask(target_team())
        # exact type: VariableRotation subclasses StrictRotation, so isinstance
        # would not distinguish them -- the strict path must return the base.
        self.assertIs(type(get_active_rotation(task)), StrictRotation)

    def test_returns_variable_when_on(self):
        task = FakeTask(target_team(), char_config=dict(ON))
        self.assertIs(type(get_active_rotation(task)), VariableRotation)

    def test_variable_instance_is_cached(self):
        task = FakeTask(target_team())
        self.assertIs(get_variable_rotation(task), get_variable_rotation(task))


class TestComputeWindow(unittest.TestCase):

    def test_unwindowed_beat_is_quickswap_zero(self):
        beat = Beat('aug_open', 'Augusta', intro=False, outro=False)
        self.assertEqual(compute_window(beat, FakeChar('Augusta', FakeTask([]))), 0)

    def test_augusta_intro_extends_to_14_on_iuno_intro(self):
        beat = Beat('aug_burst', 'Augusta', intro=True, outro=True)
        char = FakeChar('Augusta', FakeTask([]), has_sub_dps_intro=True, outro='char_iuno')
        self.assertEqual(compute_window(beat, char), 14)

    def test_no_extend_without_the_condition(self):
        beat = Beat('aug_burst', 'Augusta', intro=True, outro=True)
        # not holding a sub-dps intro -> stays a quickswap
        char = FakeChar('Augusta', FakeTask([]), has_sub_dps_intro=False)
        self.assertEqual(compute_window(beat, char), 0)

    def test_takes_max_of_base_and_all_firing_rules(self):
        beat = Beat('x', 'Augusta', intro=False, outro=False)
        win = Window(base=5, extend=[Extension('a', lambda c: True, 3),
                                     Extension('b', lambda c: True, 20)])
        with patch.dict(WINDOWS, {'x': win}, clear=False):
            self.assertEqual(compute_window(beat, FakeChar('Augusta', FakeTask([]))), 20)

    def test_bad_predicate_is_ignored_not_fatal(self):
        beat = Beat('x', 'Augusta', intro=False, outro=False)
        def boom(_):
            raise RuntimeError('predicate blew up')
        win = Window(base=2, extend=[Extension('boom', boom, 99)])
        with patch.dict(WINDOWS, {'x': win}, clear=False):
            self.assertEqual(compute_window(beat, FakeChar('Augusta', FakeTask([]))), 2)


class TestRunCurrentWindows(unittest.TestCase):

    def _rot(self, char_config=None):
        cfg = dict(ON) if char_config is None else dict(char_config)
        return VariableRotation(FakeTask(target_team(), char_config=cfg))

    def test_inactive_returns_false_and_runs_no_beat(self):
        rot = self._rot(char_config={})  # variable off
        char = FakeChar('Augusta', rot.task)
        self.assertFalse(rot.run_current(char))
        self.assertEqual(char.beats, [])

    def test_zero_window_quickswaps_like_strict(self):
        rot = self._rot()
        char = FakeChar('Augusta', rot.task)  # aug_open: not windowed -> 0
        self.assertTrue(rot.run_current(char))
        self.assertEqual(char.beats, ['aug_open'])
        self.assertEqual(char.switches, [False])   # non-outro -> plain swap
        self.assertEqual(rot.index, 1)             # advanced

    def test_outro_beat_forces_outro_when_con_full(self):
        rot = self._rot()
        rot._last_combat_start = rot.task.combat_start  # skip the new-combat rewind
        rot.index = 6  # sk_open2: ShoreKeeper, outro=True, not windowed
        char = FakeChar('ShoreKeeper', rot.task, con_full=True)
        self.assertTrue(rot.run_current(char))
        self.assertEqual(char.switches, [True])    # free_intro outro

    def test_extended_window_dwells_before_switching(self):
        rot = self._rot()
        char = FakeChar('Augusta', rot.task)
        # give aug_open a tiny real window so the dwell runs but the test is fast
        with patch.dict(WINDOWS, {'aug_open': Window(0.05, [])}, clear=False):
            start = time.time()
            self.assertTrue(rot.run_current(char))
            elapsed = time.time() - start
        self.assertGreaterEqual(elapsed, 0.05)     # it actually held the field
        self.assertGreater(char.task.clicks, 0)    # ...doing productive filler
        self.assertEqual(char.switches, [False])


class _ToppedChar:
    """Stand-in for reactive_outro_topoff: records top-off attacks; topping off
    reaches full."""

    def __init__(self, task, con=0.0, con_full=False):
        self.task = task
        self._con = con
        self._con_full = con_full
        self.attacks = []
        self.built = False

    def get_current_con(self):
        return self._con

    def is_con_full(self):
        return self._con_full

    def continues_normal_attack(self, duration, **kwargs):
        self.attacks.append((duration, kwargs))
        self._con = 1.0
        self._con_full = True

    def flying(self):
        return False

    def wait_down(self, *a, **k):
        pass

    # build_concerto() deps for the aggressive (high-yield) top-off path: all the
    # big sources are on cooldown here, so it falls to click(), which we treat as
    # reaching full so topoff_concerto returns promptly.
    def liberation_available(self, *a, **k):
        return False

    def echo_available(self, *a, **k):
        return False

    def resonance_available(self, *a, **k):
        return False

    def click_liberation(self, *a, **k):
        return False

    def click_echo(self, *a, **k):
        pass

    def send_resonance_key(self, *a, **k):
        pass

    def click(self, *a, **k):
        self.built = True
        self._con_full = True


class TestReactiveOutroTopoff(unittest.TestCase):
    """reactive_outro_topoff: finish a near-full ring so a REACTIVE swap outros,
    but stay inert while the scripted rotation is driving."""

    def _active_task(self):
        # target team + variable off -> strict rotation active by default
        return FakeTask(target_team())

    def _inactive_task(self):
        # team mismatch -> neither strict nor variable is active (reactive phase)
        return FakeTask([make_char('Augusta'), make_char('Iuno'), make_char('Verina')])

    def test_noop_while_scripted_rotation_active(self):
        char = _ToppedChar(self._active_task(), con=0.85, con_full=False)
        kwargs = {}
        reactive_outro_topoff(char, kwargs)
        self.assertEqual(char.attacks, [])   # coordinator handles outros
        self.assertEqual(kwargs, {})         # no forced outro

    def test_tops_off_near_full_and_forces_outro(self):
        char = _ToppedChar(self._inactive_task(), con=0.85, con_full=False)
        kwargs = {}
        reactive_outro_topoff(char, kwargs)
        self.assertEqual(len(char.attacks), 1)
        self.assertTrue(char.attacks[0][1].get('until_con_full'))
        self.assertEqual(kwargs, {'free_intro': True})

    def test_already_full_forces_outro_without_topoff(self):
        char = _ToppedChar(self._inactive_task(), con=1.0, con_full=True)
        kwargs = {}
        reactive_outro_topoff(char, kwargs)
        self.assertEqual(char.attacks, [])   # already full -> no wasted top-off
        self.assertEqual(kwargs, {'free_intro': True})

    def test_low_con_neither_tops_off_nor_forces(self):
        char = _ToppedChar(self._inactive_task(), con=0.5, con_full=False)
        kwargs = {}
        reactive_outro_topoff(char, kwargs)
        self.assertEqual(char.attacks, [])   # below threshold
        self.assertEqual(kwargs, {})         # not full -> plain swap

    def test_aggressive_builds_concerto_instead_of_basics(self):
        # aggressive=True uses the high-yield build_concerto top-off, NOT plain
        # basics (Iuno's concerto barely moves on basics). Lower threshold catches
        # her earlier.
        task = self._inactive_task()
        char = _ToppedChar(task, con=0.65, con_full=False)
        kwargs = {}
        reactive_outro_topoff(char, kwargs, threshold=0.6, aggressive=True)
        self.assertEqual(char.attacks, [])        # did NOT use basics
        self.assertTrue(char.built)               # used build_concerto instead
        self.assertEqual(kwargs, {'free_intro': True})  # reached full -> outro forced

    def test_aggressive_below_threshold_is_noop(self):
        task = self._inactive_task()
        char = _ToppedChar(task, con=0.5, con_full=False)
        kwargs = {}
        reactive_outro_topoff(char, kwargs, threshold=0.6, aggressive=True)
        self.assertEqual(char.attacks, [])
        self.assertEqual(task.wait_until_calls, [])       # below 0.6 -> no build
        self.assertEqual(kwargs, {})


if __name__ == '__main__':
    unittest.main()

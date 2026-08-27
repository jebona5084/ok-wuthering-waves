import unittest
from types import SimpleNamespace

from config import config
from ok.test.TaskTestCase import TaskTestCase
from src.task.AutoCombatTask import AutoCombatTask

config['debug'] = True


class TestCD(TaskTestCase):
    task_class = AutoCombatTask
    config = config

    def assert_cd_values(self, image, expected):
        self.task.do_reset_to_false()
        self.set_image(image)
        # the task instance is shared across tests: restore chars so the
        # incomplete stub doesn't leak into tests that run load_chars()
        self.addCleanup(setattr, self.task, 'chars', self.task.chars)
        self.task.chars = [SimpleNamespace(index=0, is_current_char=True), None, None]
        self.task.refresh_cd()

        actual = {
            name: self.task.cds[0][name]
            for name in ('resonance', 'echo', 'liberation')
        }
        self.assertEqual(expected, actual)

    def test_cd_values_from_screenshots(self):
        cases = {
            'tests/images/in_combat.png': {
                'resonance': 0,
                'echo': 18.1,
                'liberation': 0,
            },
            'tests/images/in_combat3.png': {
                'resonance': 1.3,
                'echo': 14.1,
                'liberation': 18.0,
            },
            'tests/images/all_cd_1080p.png': {
                'resonance': 13.6,
                'echo': 18.4,
                'liberation': 22.6,
            },
            'tests/images/con_full2.png': {
                'resonance': 1.9,
                'echo': 21.3,
                'liberation': 0,
            },
        }

        for image, expected in cases.items():
            with self.subTest(image=image):
                self.assert_cd_values(image, expected)

    def count_ocr(self):
        calls = {'n': 0}
        orig = self.task.ocr

        def counting_ocr(*a, **k):
            calls['n'] += 1
            return orig(*a, **k)

        self.task.ocr = counting_ocr
        self.addCleanup(setattr, self.task, 'ocr', orig)
        return calls

    def test_cd_reading_is_cached_across_frames(self):
        self.task.do_reset_to_false()
        self.set_image('tests/images/in_combat.png')
        self.task.load_chars()
        calls = self.count_ocr()
        first = self.task.get_cd('echo')
        self.task.scene.reset()  # next combat-loop frame
        second = self.task.get_cd('echo')
        self.assertEqual(calls['n'], 1)
        self.assertTrue(self.task.has_cd('echo'))
        self.assertLessEqual(second, first)  # cached value counts down, not refreshed

    def test_invalidate_cd_forces_reread(self):
        self.task.do_reset_to_false()
        self.set_image('tests/images/in_combat.png')
        self.task.load_chars()
        calls = self.count_ocr()
        self.task.get_cd('echo')
        self.task.invalidate_cd(self.task.get_current_char().index)
        self.task.get_cd('echo')
        self.assertEqual(calls['n'], 2)


if __name__ == '__main__':
    unittest.main()

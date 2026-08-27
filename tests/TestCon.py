import time
import unittest
from config import config
from ok.test.TaskTestCase import TaskTestCase
from src.task.AutoCombatTask import AutoCombatTask

config['debug'] = True


class TestCon(TaskTestCase):
    task_class = AutoCombatTask
    config = config

    def assert_confirmed_full(self, image):
        """Full is reported only after two clean matching frames (see
        resolve_con_reading). The test harness serves one static buffer, so
        nudge a corner pixel to give the second read a new frame identity
        (production frames always differ); the ring itself is untouched."""
        self.task.do_reset_to_false()
        self.set_image(image)
        self.task.load_chars()
        char = self.task.get_current_char()
        # the char has been fighting, not fresh off a switch-in: keeps the
        # fully-formed ring from being rejected as the switch-in decoy overlay
        char.last_switch_in_time = time.time() - 10
        char.get_current_con()  # first clean full: arms the confirm
        # second distinct frame, same ring: the frame identity hashes the con
        # box crop, so nudge that crop's corner pixel (off the ring annulus)
        box = self.task.get_con_box()
        self.task.frame[box.y, box.x, 0] ^= 1
        self.assertTrue(char.is_con_full())

    def test_con_full(self):
        self.assert_confirmed_full('tests/images/con_full.png')

    def test_con_full2(self):
        self.assert_confirmed_full('tests/images/in_combat.png')

    def test_con_full3(self):
        self.assert_confirmed_full('tests/images/all_cd_1080p.png')

    def test_con_full4(self):
        self.task.do_reset_to_false()
        self.set_image('tests/images/absorb.png')
        self.task.load_chars()
        con_full = self.task.get_current_char().is_con_full()
        self.assertFalse(con_full)

    def test_con_full5(self):
        self.task.do_reset_to_false()
        self.set_image('tests/images/angle_130.png')
        self.task.load_chars()
        con = self.task.get_current_char().get_current_con()
        self.task.log_info(f'{self.task.get_current_char()} con = {con}')
        con_full = self.task.get_current_char().is_con_full()
        self.assertFalse(con_full)

    def test_con_full6(self):
        self.assert_confirmed_full('tests/images/con_full2.png')


if __name__ == '__main__':
    unittest.main()

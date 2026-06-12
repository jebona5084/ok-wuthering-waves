import time

from src.char.BaseChar import BaseChar, SwitchPriority
from src.char.ForteMixin import ForteMixin


class Ciaccona(ForteMixin, BaseChar):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.intro_motion_freeze_duration = 0.73
        self.attribute = 0
        self.in_liberation = False
        self.cartethyia = None
        self.outrotime = -1

    def skip_combat_check(self):
        return self.time_elapsed_accounting_for_freeze(self.last_liberation) < 2

    def reset_state(self):
        super().reset_state()
        self.attribute = 0
        self.cartethyia = None

    def do_perform(self):
        self.in_liberation = False
        wait = False
        jump = True
        if self.attribute == 0:
            self.decide_teammate()
        if self.has_intro:
            self.continues_normal_attack(0.8)
            if not self.need_fast_perform():
                self.continues_normal_attack(0.7)
        if self.current_echo() < 0.22:
            self.click_echo(time_out=0)
        if not self.has_intro and not self.need_fast_perform() and not self.is_mouse_forte_full():
            self.click_jump_with_click(0.4)
            self.task.wait_until(lambda: not self.flying(), post_action=self.click_with_interval, time_out=1.2)
            self.continues_normal_attack(0.2)
        if self.click_resonance()[0]:
            jump = False
            wait = True
        if self.judge_forte() >= 3:
            if jump:
                start = time.time()
                while not self.flying():
                    self.task.jump(after_sleep=0.01)
                    if time.time() - start > 0.3:
                        break
                    self.task.next_frame()
            self.heavy_click_forte(check_fun=self.is_mouse_forte_full)
            wait = True
        if self.liberation_available():
            if wait:
                self.sleep(0.4)
            if self.click_liberation():
                self.in_liberation = True
                if self.attribute == 2:
                    self.continues_click_a(0.6)
        if not self.in_liberation and self.current_echo() > 0.25:
            self.click_echo()
        self.switch_next_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if self.attribute == 2 and self.in_liberation and self.time_elapsed_accounting_for_freeze(
                self.last_liberation) < 20:
            return SwitchPriority.NO
        if self.attribute == 3 and self.in_liberation and (
                self.time_elapsed_accounting_for_freeze(self.last_liberation) < 8 or not self.cartethyia.is_cartethyia):
            return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def click_jump_with_click(self, delay=0.1):
        start = time.time()
        click = 1
        while True:
            if time.time() - start > delay:
                break
            if click == 0:
                self.task.jump(after_sleep=0.01)
            else:
                self.click()
            click = 1 - click
            self.check_combat()
            self.task.next_frame()

    def continues_click_a(self, duration=0.6):
        start = time.time()
        while time.time() - start < duration:
            self.task.send_key(key='a')

    def judge_forte(self):
        if self.is_mouse_forte_full():
            return 3
        box = self.task.box_of_screen_scaled(3840, 2160, 1612, 1987, 2188, 2008, name='ciaccona_forte', hcenter=True)
        return self.calculate_forte_num(ciaccona_forte_color, box, 3, 12, 14, 100)

    def decide_teammate(self):
        from src.char.Phoebe import Phoebe
        from src.char.Zani import Zani
        from src.char.Cartethyia import Cartethyia
        for i, char in enumerate(self.task.chars):
            self.logger.debug(f'ciaccona teammate char: {char.char_name}')
            if isinstance(char, Cartethyia):
                self.logger.debug('ciaccona set attribute: wind dot')
                self.cartethyia = char
                self.attribute = 3
                return
            if isinstance(char, (Phoebe, Zani)):
                self.logger.debug('ciaccona set attribute: light dot')
                self.attribute = 2
                return
        self.logger.debug('ciaccona set attribute: wind dot')
        self.attribute = 1

    def switch_next_char(self, *args, **kwargs):
        if self.is_con_full():
            self.outrotime = time.time()
        return super().switch_next_char(*args, **kwargs)

    def in_outro(self):
        return self.time_elapsed_accounting_for_freeze(self.outrotime) < 30

    def need_fast_perform(self):
        from src.char.Cartethyia import Cartethyia
        if self.task.has_char(Cartethyia) and hasattr(Cartethyia, 'is_cartethyia'):
            return Cartethyia.is_cartethyia
        return False


ciaccona_forte_color = {
    'r': (70, 100),
    'g': (240, 255),
    'b': (180, 210)
}

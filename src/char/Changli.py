import time

from src.char.BaseChar import BaseChar, SwitchPriority, forte_white_color
from src.char.ForteMixin import ForteMixin


class Changli(ForteMixin, BaseChar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enhanced_normal = False

    def reset_state(self):
        super().reset_state()
        self.enhanced_normal = False

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if has_intro and current_char and current_char.char_name in {'char_brant'}:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def do_perform(self):
        outro = False
        forte = -1
        self.check_f_on_switch = True
        if self.has_intro:
            self.continues_normal_attack(0.3)
            self.enhanced_normal = True
        forte = self.judge_forte()
        if self.enhanced_normal:
            self.logger.debug('Changli has enhanced')
            self.continues_normal_attack(0.2)
            self.sleep(0.2)
            if self.check_outro() in {'char_brant'}:
                self.sleep(0.2)
                self.do_perform_outro(self.judge_forte())
                return self.switch_next_char()
            if forte == 3:
                self.sleep(0.2)
                forte = self.judge_forte()
        self.enhanced_normal = False
        if forte == 4 or self.is_mouse_forte_full():
            if self.flying():
                self.heavy_attack()
            self.heavy_click_forte(check_fun=self.is_mouse_forte_full)
            self.check_f_on_switch = False
            self.check_combat()
            return self.switch_next_char()
        if not (forte >= 3 and self.resonance_available()) and self.liberation_available():
            if self.liberation_and_heavy():
                self.check_f_on_switch = False
                return self.switch_next_char()
        if self.flick_resonance(send_click=False):
            self.enhanced_normal = True
            return self.switch_next_char()
        if self.click_echo():
            return self.switch_next_char()
        self.continues_normal_attack(0.1)
        self.switch_next_char()

    def do_perform_outro(self, forte):
        if forte == 3:
            start = time.time()
            forte_time = start
            res = True
            while time.time() - start < 5:
                if res and self.flick_resonance(send_click=False):
                    res = False
                    continue
                self.click(interval=0.1)
                if self.is_mouse_forte_full():
                    if time.time() - forte_time > 0.2:
                        break
                else:
                    forte_time = time.time()
                self.check_combat()
                self.task.next_frame()
            if self.is_mouse_forte_full():
                self.heavy_click_forte(check_fun=self.is_mouse_forte_full)
                self.sleep(1)
        elif forte >= 4 or self.is_mouse_forte_full():
            if self.flying():
                self.heavy_attack()
            self.heavy_click_forte(check_fun=self.is_mouse_forte_full)
            forte = 0
            self.sleep(1)
        if self.liberation_available() and self.liberation_and_heavy():
            self.sleep(0.6)
            forte = 0
        if forte < 3 and self.flick_resonance(send_click=False):
            self.enhanced_normal = True
            return
        self.click_echo()

    def judge_forte(self):
        if self.is_mouse_forte_full():
            return 4
        box = self.task.box_of_screen_scaled(3840, 2160, 1633, 2004, 2160, 2016, name='changli_forte', hcenter=True)
        return self.calculate_forte_num(changli_red_color, box, 4, 9, 11, 400)

    def liberation_and_heavy(self, con_less_than=-1, send_click=False, wait_if_cd_ready=0, timeout=5):
        if con_less_than > 0 and self.get_current_con() > con_less_than:
            return False
        self.logger.debug('click_liberation start')
        start = time.time()
        last_click = 0
        clicked = False
        while time.time() - start < wait_if_cd_ready and not self.liberation_available() and not self.has_cd('liberation'):
            if send_click:
                self.click(interval=0.1)
            self.task.next_frame()
        while self.liberation_available() and self.task.in_team()[0]:
            now = time.time()
            if now - last_click > 0.1:
                self.send_liberation_key()
                if not clicked:
                    clicked = True
                    self.record_liberation_use()
                last_click = now
            if time.time() - start > timeout:
                self.task.raise_not_in_combat('too long clicking a liberation')
            self.task.next_frame()
        if clicked:
            if self.task.wait_until(lambda: not self.task.in_team()[0], time_out=0.4):
                self.task.in_liberation = True
                self.logger.debug('not in_team successfully casted liberation')
            else:
                self.task.in_liberation = False
                self.logger.error('clicked liberation but no effect')
                return False
        start = time.time()
        hold = False
        while not self.task.in_team()[0]:
            self.task.in_liberation = True
            if not clicked:
                clicked = True
                self.record_liberation_use()
            if send_click:
                self.click(interval=0.1)
            if time.time() - start > 1.5 and not hold:
                self.task.mouse_down()
                hold = True
            if time.time() - start > 7:
                self.task.in_liberation = False
                self.task.raise_not_in_combat('too long a liberation, the boss was killed by the liberation')
            self.task.next_frame()
        duration = time.time() - start
        self.add_freeze_duration(start, duration)
        self.task.in_liberation = False
        if clicked:
            self.logger.info(f'click_liberation end {duration}')
            self.task.wait_until(lambda: self.task.in_team()[0] and self.is_mouse_forte_full(), time_out=0.6)
            self.task.wait_until(lambda: self.task.in_team()[0] and not self.is_mouse_forte_full(), time_out=0.6)
        self.task.mouse_up()
        self.check_combat()
        return clicked

    def flick_resonance(self, time_out=0.2, send_click=True):
        if send_click and self.resonance_available():
            self.task.wait_until(lambda: self.current_resonance() > 0, post_action=self.click_with_interval,
                                 time_out=0.2)
        if self.current_resonance() > 0 and self.resonance_available():
            self.task.wait_until(lambda: not self.resonance_available(), post_action=self.send_resonance_key,
                                 time_out=time_out)
            return True
        return False


changli_red_color = {
    'r': (240, 255),
    'g': (85, 105),
    'b': (95, 115)
}

import time

from src.char.BaseChar import BaseChar, SwitchPriority


class Xiangliyao(BaseChar):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.liberation_time = 0

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if has_intro and current_char and current_char.char_name in {'char_calcharo'}:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def do_perform(self):
        self.wait_down()
        if self.click_liberation():
            self.liberation_time = time.time()
        if self.still_in_liberation():
            # the window-ending resonance flows straight into the switch,
            # which cancels its recovery; no top-off here to keep it tight
            while not self.click_resonance(send_click=True)[0]:
                self.continues_normal_attack(1)
        else:
            if self.echo_available():
                self.logger.debug('click_echo')
                self.click_echo()
            else:
                self.click_resonance(send_click=True)
            self.top_off_con()
        self.switch_next_char()

    def still_in_liberation(self):
        return self.time_elapsed_accounting_for_freeze(self.liberation_time) < 25

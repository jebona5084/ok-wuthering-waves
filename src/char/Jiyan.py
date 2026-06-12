import time

from src.char.BaseChar import BaseChar, SwitchPriority


class Jiyan(BaseChar):

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if has_intro and current_char and current_char.char_name in {'char_mortefi'}:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def do_perform(self):
        if self.has_intro:
            self.logger.debug('jiyan wait intro')
            self.continues_normal_attack(duration=2.0)
            # fly check not work for jiyan
        # echo first so a heavy-tagged echo lands inside the outro deepen
        self.click_echo(time_out=0)
        if self.liberation_available():
            # skill into liberation: the liberation cast cancels its backswing
            self.click_resonance()
        if self.click_liberation():
            start = time.time()
            while time.time() - start < 12:
                if self.click_resonance()[0]:
                    self.task.middle_click_relative(0.5, 0.5)
                self.normal_attack()
            self.top_off_con()
            return self.switch_next_char()
        i = 0
        while not self.is_forte_full() and not self.is_con_full():
            if i % 4 == 0:
                self.heavy_attack()
                if self.resonance_available() or self.echo_available():
                    self.task.middle_click_relative(0.5, 0.5)
                    break
                i = 0
            self.normal_attack()
            i += 1
        if not self.is_forte_full() and self.resonance_available():
            self.click_resonance(post_sleep=1.0)
        if self.echo_available():
            self.click_echo()
        self.top_off_con()
        self.switch_next_char()

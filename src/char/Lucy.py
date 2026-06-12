import time

from src.char.BaseChar import BaseChar, SwitchPriority


class Lucy(BaseChar):
    ALGORITHM_TIMEOUT = 5.0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_algorithm = -1

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if has_intro and current_char and current_char.char_name in {'char_rebecca'}:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def opener_skills(self):
        """Payload then Pulse Interference right after the intro: the two
        skill casts start passive TCP regen and light the signature weapon
        passives before the basic chain. No-ops instantly when on cooldown."""
        if self.click_resonance(time_out=0.8)[0]:
            self.continues_normal_attack(0.35)
            self.click_resonance(time_out=0.8)

    def do_perform(self):
        if self.has_intro:
            self.continues_normal_attack(1.0)
            self.opener_skills()
        elif self.flying():
            self.wait_down()

        self.click_echo(time_out=0)

        if self.is_forte_full():
            if self.click_resonance(time_out=0.8)[0]:
                self.last_algorithm = time.time()
                self.perform_algorithm_compaction()
                self.top_off_con()
                return self.switch_next_char()

        if not self.need_fast_perform() and self.click_liberation(wait_if_cd_ready=0):
            self.f_break()
            return self.switch_next_char()

        if self.click_resonance(time_out=0.8)[0]:
            start = time.time()
            while self.time_elapsed_accounting_for_freeze(start) < 1.4:
                if self.is_forte_full():
                    if self.click_resonance(time_out=0.8)[0]:
                        self.last_algorithm = time.time()
                        self.perform_algorithm_compaction()
                    break
                self.click()
                self.task.next_frame()
            self.top_off_con()
            return self.switch_next_char()

        self.continues_normal_attack(0.8)
        self.top_off_con()
        self.switch_next_char()

    def perform_algorithm_compaction(self):
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.ALGORITHM_TIMEOUT:
            self.cycle_start()
            if self.need_fast_perform():
                return False
            if self.is_forte_full() and self.perform_multi_threading():
                if self.click_liberation(wait_if_cd_ready=0):
                    self.f_break()
                return True
            if self.click_liberation(wait_if_cd_ready=0):
                self.f_break()
                return True
            self.click()
            self.cycle_sleep()
        return False

    def perform_multi_threading(self):
        if not self.heavy_click_forte(self.is_forte_full):
            return False
        self.continues_normal_attack(0.45)
        return True

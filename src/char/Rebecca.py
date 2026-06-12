import time

from src.char.BaseChar import BaseChar


class Rebecca(BaseChar):
    HMG_TIME = 5.2

    def do_perform(self):
        if self.has_intro:
            self.continues_normal_attack(1.0)
        elif self.flying():
            self.wait_down()

        self.click_echo(time_out=0)
        self.perform_enhanced_heavy()

        if self.click_resonance(time_out=0.8)[0]:
            self.continues_normal_attack(0.35)

        self.perform_enhanced_heavy()

        if not self.need_fast_perform() and self.click_liberation(wait_if_cd_ready=0):
            if self.perform_hmg_mode():
                self.cast_fireworks_finisher()
            return self.switch_next_char()

        self.continues_normal_attack(0.7)
        self.top_off_con()
        self.switch_next_char()

    def perform_enhanced_heavy(self):
        if self.heavy_click_forte(self.is_forte_full):
            self.continues_normal_attack(0.25)
            return True
        return False

    def perform_hmg_mode(self):
        start = time.time()
        last_liberation = start
        while self.time_elapsed_accounting_for_freeze(start) < self.HMG_TIME:
            if self.need_fast_perform():
                return False
            self.click(interval=0.08)
            now = time.time()
            if now - last_liberation > 0.9:
                self.send_liberation_key()
                last_liberation = now
            self.check_combat()
            self.task.next_frame()
        return True

    def cast_fireworks_finisher(self):
        """Hold liberation at the end of the HMG window to cast BOOM!
        Fireworks!, then give the damage frames a short commit window; the
        switch that follows swap-cancels the rest of the finisher and
        carries ~10 concerto into her next rotation."""
        self.logger.debug('fireworks finisher')
        self.send_liberation_key(down_time=0.55)
        self.sleep(0.25)

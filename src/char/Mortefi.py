from src.char.BaseChar import BaseChar


class Mortefi(BaseChar):
    def do_perform(self):
        self.wait_down()
        if self.click_resonance()[0]:
            self.continues_normal_attack(0.3)
            self.click_resonance()
        self.click_echo()
        # Marcato: the liberation cast right after cancels its recovery
        self.heavy_click_forte(self.is_forte_full)
        self.top_off_con()
        # liberation last: Violent Finale persists off-field, so casting it
        # on the way out maximizes overlap with the next window; the switch
        # swap-cancels its tail and the outro deepen lands fresh
        self.click_liberation(wait_if_cd_ready=1)
        self.switch_next_char()

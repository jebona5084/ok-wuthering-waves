from src.char.BaseChar import BaseChar


class Yinlin(BaseChar):
    def do_perform(self):
        if self.has_intro:
            self.sleep(0.4)
        if self.is_mouse_forte_full():
            if not self.has_intro:
                self.normal_attack()
            self.heavy_attack()
            self.sleep(0.4)
        elif self.click_resonance(send_click=False)[0]:
            self.sleep(0.1)
        elif self.echo_available():
            self.click_echo()
        else:
            self.heavy_attack()
        self.top_off_con()
        # liberation last: the switch right after swap-cancels its tail, and
        # her outro deepen lands the moment the buffed window begins
        self.click_liberation()
        self.switch_next_char()

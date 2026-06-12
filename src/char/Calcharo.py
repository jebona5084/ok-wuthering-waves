import time

from src.char.BaseChar import BaseChar, SwitchPriority


class Calcharo(BaseChar):
    LIB_WINDOW = 12
    CHAIN_BASICS_TIME = 2.1

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if has_intro and current_char and current_char.char_name in {'char_yinlin'}:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def do_perform(self):
        if self.has_intro:
            self.logger.debug('Calcharo wait intro animation')
            self.sleep(1)
            self.task.wait_in_team_and_world(time_out=3, raise_if_not_found=False)
            self.check_combat()
        # echo first: a relay echo (e.g. Hyvatia) must be summoned before the
        # outro for its buff to ride onto the next character
        self.click_echo(time_out=0)
        if not self.need_fast_perform() and self.click_liberation():
            self.perform_deathblade_window()
            self.top_off_con()
            return self.switch_next_char()
        self.click_resonance()
        self.top_off_con()
        self.switch_next_char()

    def perform_deathblade_window(self):
        """Deathblade Gear: chain 5 enhanced basics into a Heavy Attack
        (Death Messenger) for the whole window. A dodge mid-string resets
        the 5-basic count, so the tap-dodge is spent only on a Death
        Messenger recovery with another chain to follow; the final one
        rides the switch-cancel for free. Incoming deepen buffs expire on
        swap, so the window stays on field unless a teammate's genuine
        MUST breaks it."""
        start = time.time()
        while self.time_elapsed_accounting_for_freeze(start) < self.LIB_WINDOW:
            if self.need_fast_perform():
                return
            self.continues_normal_attack(self.CHAIN_BASICS_TIME)
            self.heavy_attack()
            if self.time_elapsed_accounting_for_freeze(start) < self.LIB_WINDOW:
                self.continues_right_click(0.05)
        self.click_resonance()

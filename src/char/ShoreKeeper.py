import time

from src.char.BaseChar import BaseChar, SwitchPriority


class ShoreKeeper(BaseChar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outrotime = -1
        self.dodge_count = 0
        self.attribute = 0

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        from src.combat.StrictRotation import get_strict_rotation, MUST, NO
        rot = get_strict_rotation(self.task)
        if rot.is_active():
            priority = rot.priority_for(self.name)
            if priority == MUST:
                return SwitchPriority.MUST
            if priority == NO:
                return SwitchPriority.NO
        self.decide_teammate()
        current_name = current_char.char_name if current_char else None
        if self.attribute == 2 and has_intro and current_name in {'Augusta', 'char_augusta'}:
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    # Concerto top-off window, mirrored from the reactive quickswap chars in the
    # custom collection (Mornye/Jiyan/custom ShoreKeeper all use 0.7): when the
    # ring is close but not quite full on the way out, spend up to 0.8s of
    # forte-feeding basics to finish it so the swap fires as a real OUTRO (which
    # transfers her team buff). Below the threshold the ring is too far to close
    # in the budget, so skip the top-off and just swap.
    TOP_OFF_THRESHOLD = 0.7

    def skip_combat_check(self):
        return self.has_intro or self.flying()

    def dodge_cancel(self):
        """Tap-dodge to cut a cast's landing/recovery (e.g. the Stellarealm
        deploy) when grounded, so the next action starts sooner. Ported from the
        reactive custom build; the built-in already dodges in this exact state
        (auto_dodge), so it does not knock her out of anything she needs."""
        if not self.flying():
            self.continues_right_click(0.05)
            self.sleep(0.05)

    def decide_teammate(self):
        from src.char.Augusta import Augusta
        if self.attribute > 0:
            return
        if self.task.has_char(Augusta):
            self.attribute = 2
        else:
            self.attribute = 1

    def do_perform(self):
        from src.combat.StrictRotation import get_strict_rotation
        if get_strict_rotation(self.task).run_current(self):
            return
        self._do_perform_default()

    def _do_perform_default(self):
        if self.has_intro:
            self._intro_wait()
        self.continues_normal_attack(2.2)
        self.click_echo(time_out=0)
        if self.click_liberation():
            self.dodge_cancel()  # cut the Stellarealm-deploy recovery
        # Spend BOTH skill and forte every cycle rather than the forte only when
        # the skill failed: her enhanced heavy is a big concerto source, so
        # checking it every cycle (not just on a skill miss) cashes it in before
        # it overcaps. heavy_click_forte no-ops when the gauge is not charged.
        self.click_resonance()
        self.heavy_click_forte(self.is_mouse_forte_full)
        self.switch_next_char()

    def _intro_wait(self):
        self.task.skip_combat_check = True
        try:
            self.logger.debug('ShoreKeeper wait intro animation')
            time.sleep(0.1)
            if not self.task.in_team_and_world():
                self.task.wait_in_team_and_world(time_out=4, raise_if_not_found=False)
            else:
                self.continues_normal_attack(1.2)
        finally:
            self.task.skip_combat_check = False

    def perform_beat(self, beat):
        """Execute one strict-rotation beat (see src/combat/StrictRotation.py).

        Each beat spends ShoreKeeper's full concerto-building kit (echo, lib,
        skill, forte) so the ring is naturally full by the outro swap -- there is
        no busy-wait top-off, which would stall and break the rotation.
        """
        from src.combat.StrictRotation import basic_attacks
        if beat.name == 'sk_open':
            # 3. echo, ba123, lib, ba12, ha, skill
            # Echo first: it is ShoreKeeper's main concerto source (her basic
            # attacks generate almost none), so without it she never builds the
            # concerto needed to outro and apply her outro buff. time_out=0 only
            # fires when the echo is off cooldown, so it is safe to always call.
            self.click_echo(time_out=0)
            # forte_check: spend her enhanced heavy the moment it charges during
            # the basics (it is a big concerto source) instead of letting it
            # overcap until the next scripted heavy.
            basic_attacks(self, 3, forte_check=self.is_mouse_forte_full)
            if self.click_liberation():
                self.dodge_cancel()
            basic_attacks(self, 2, forte_check=self.is_mouse_forte_full)
            self.heavy_attack()
            self.click_resonance()
        elif beat.name == 'sk_open2':
            # 7. echo, ba12345, ha, outro
            self.click_echo(time_out=0)
            basic_attacks(self, 5, forte_check=self.is_mouse_forte_full)
            self.heavy_attack()
            self.task.jump(after_sleep=0.2)
            # sk_open2 alone omitted lib+skill, so it entered the central top-off
            # with the least concerto banked. Spend them here too (each is
            # frame-checked and a no-op when on cooldown), mirroring sk_open.
            if self.click_liberation():
                self.dodge_cancel()
            self._spend_skill_and_forte()
            self.continues_normal_attack(1.6)
            self.heavy_attack()
        elif beat.name in ('sk_intro', 'sk_loop'):
            # 10 / 16. super intro, build concerto, outro
            if beat.intro:
                self._intro_wait()
            self.click_echo(time_out=0)
            if self.click_liberation():
                self.dodge_cancel()
            self._spend_skill_and_forte()
        else:  # defensive: unknown beat
            self.click_echo(time_out=0)
            if self.click_liberation():
                self.dodge_cancel()
            self._spend_skill_and_forte()

    def _spend_skill_and_forte(self):
        """Spend BOTH skill and forte for concerto, not either/or.

        ShoreKeeper is a low-damage healer, so her held forte is one of her
        biggest concerto sources. The previous ``skill OR forte`` (only used
        forte if the skill failed to fire) meant forte was skipped on every beat
        where the skill landed, leaving her short of a full ring for the outro.
        heavy_click_forte no-ops when the forte gauge is not charged, so spending
        both is safe and adds no dead time.
        """
        self.click_resonance()
        self.heavy_click_forte(self.is_mouse_forte_full)

    def switch_next_char(self, *args, **kwargs):
        # During the strict rotation the coordinator already fills the ring
        # before calling this (con == 1.0), so the top-off is skipped there; it
        # only bites in the reactive phase (after STOP_AFTER_FIRST_ROTATION hands
        # the sustained fight to the reactive engine), where an almost-full exit
        # would otherwise downgrade to a plain swap and drop her outro buff.
        con = self.get_current_con()
        if self.TOP_OFF_THRESHOLD <= con < 1:
            self.continues_normal_attack(0.8, until_con_full=True)
        if self.is_con_full():
            self.outrotime = time.time()
            self.dodge_count = 5
        return super().switch_next_char(*args, **kwargs)

    def auto_dodge(self, condition):
        clicked = False
        if self.time_elapsed_accounting_for_freeze(self.outrotime) < 30 and self.dodge_count > 0:
            start = time.time()
            while time.time() - start < 1.5:
                if not condition():
                    break
                self.continues_right_click(0.05)
                self.sleep(0.05)
                clicked = True
                self.task.next_frame()
        if clicked:
            self.dodge_count -= 1
            self.logger.info('ShoreKeepers auto dodge success!')
        return clicked

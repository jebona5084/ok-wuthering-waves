import time

from src.char.BaseChar import BaseChar, SwitchPriority


class ShoreKeeper(BaseChar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outrotime = -1
        self.dodge_count = 0
        self.attribute = 0

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        from src.combat.VariableRotation import get_active_rotation
        from src.combat.StrictRotation import MUST, NO
        rot = get_active_rotation(self.task)
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

    def _cast_liberation_now(self):
        """Cast her Resonance Liberation the instant it is ready, so the team buff
        it applies goes up immediately -- she was delaying it behind filler basics
        / a 2.2s attack, so the buff was still down while the cast was available.
        click_liberation no-ops when it is on cooldown, and the dodge cuts the
        Stellarealm-deploy recovery. Returns True if it fired."""
        if self.click_liberation():
            self.dodge_cancel()
            return True
        return False

    def decide_teammate(self):
        from src.char.Augusta import Augusta
        if self.attribute > 0:
            return
        if self.task.has_char(Augusta):
            self.attribute = 2
        else:
            self.attribute = 1

    def do_perform(self):
        from src.combat.VariableRotation import get_active_rotation
        if get_active_rotation(self.task).run_current(self):
            return
        self._do_perform_default()

    def _do_perform_default(self):
        if self.has_intro:
            self._intro_wait()
        # Liberation FIRST when it is ready: its team buff should be up at once,
        # not behind 2.2s of filler basics. Echo (instant, concerto) then lib.
        self.click_echo(time_out=0)
        self._cast_liberation_now()
        self.continues_normal_attack(2.2)
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

        Quickswap contract (user-verified kit map) -- she is the swap-FRIENDLY
        member of this team; her buffs are damage amplification that survives
        switching, and the Stellarealm is designed to be left:
        - Liberation (End Loop): canonical exit -- the field persists 30s at the
          cast location regardless of her being on field; swap can follow the
          cast immediately. (We front-load it instead so the field is up for her
          own slot too -- per the kit, nothing is lost either way.)
        - Resonance Skill (Chaos Theory): cast-and-leave; 20 concerto instant,
          the butterflies track autonomously and the rest trickles in off-field.
        - Forte heavy (Illation): safe once the hit registers; the converted
          butterflies keep attacking after she leaves.
        - Echo: standard swap-cancel, best right before the lib or the outro.
        - Intro (Discernment): the one thing NOT to clip early -- let the
          guaranteed-crit detonation land, then outro (_intro_wait covers this).
        - Avoid leaving mid-basic-string (wastes Empirical Data generation, not
          buffs) and never idle in Unbound Form (stamina-drain channel).
        """
        from src.combat.StrictRotation import basic_attacks, aggressive_cancel_enabled
        agg = aggressive_cancel_enabled(self.task)  # jump-cancel filler basics when on
        if beat.name == 'sk_open':
            # 3. lib (immediately if ready), echo, ba12345, ha, skill
            # Liberation up front so its team buff is up at once. Echo then feeds
            # concerto (her basics generate almost none); time_out=0 only fires
            # when the echo is off cooldown, so it is safe to always call.
            self._cast_liberation_now()
            self.click_echo(time_out=0)
            # forte_check: spend her enhanced heavy the moment it charges during
            # the basics (it is a big concerto source) instead of letting it
            # overcap until the next scripted heavy.
            basic_attacks(self, 5, forte_check=self.is_mouse_forte_full, cancel=agg)
            self.heavy_attack()
            self.click_resonance()
        elif beat.name == 'sk_open2':
            # 7. lib (immediately if ready), echo, ba12345, ha, skill+forte, outro
            # Liberation up front so its team buff is up at once; skill+forte are
            # frame-checked (no-op on cooldown) and bank the rest of the concerto.
            self._cast_liberation_now()
            self.click_echo(time_out=0)
            basic_attacks(self, 5, forte_check=self.is_mouse_forte_full, cancel=agg)
            self.heavy_attack()
            self.task.jump(after_sleep=0.2)
            self._spend_skill_and_forte()
            self.continues_normal_attack(1.6)
            self.heavy_attack()
        elif beat.name in ('sk_intro', 'sk_loop'):
            # 10 / 16. super intro, lib (immediately), build concerto, outro
            if beat.intro:
                self._intro_wait()
            self._cast_liberation_now()
            self.click_echo(time_out=0)
            self._spend_skill_and_forte()
        else:  # defensive: unknown beat
            self._cast_liberation_now()
            self.click_echo(time_out=0)
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
        # Reactive-phase outro hardening (no-op while the scripted rotation
        # drives). Her outro buff is REQUIRED by the cycle -- Augusta must carry
        # it into her burst -- and 0.8s of plain basics barely moved her ring
        # (log: swapped out at 0.89/0.74 con). Build with her real concerto
        # sources instead (echo/skill/lib via the aggressive top-off) and never
        # early-bail (mandatory), then leave via a forced outro when full.
        from src.combat.VariableRotation import reactive_outro_topoff
        reactive_outro_topoff(self, kwargs, threshold=0.6, aggressive=True,
                              mandatory=True)
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

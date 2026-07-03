import time

from src.char.BaseChar import BaseChar, CharType, get_default_buff_time


class Iuno(BaseChar):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.last_heavy = 0

    def is_c6(self):
        return self.task and self.task.char_config.get("Iuno C6")

    def get_char_type(self):
        if self.is_c6():
            return CharType.MAIN_DPS
        return super().get_char_type()

    def get_buff_time(self):
        if self.is_c6():
            return get_default_buff_time(CharType.MAIN_DPS)
        return super().get_buff_time()

    def do_perform(self):
        from src.combat.VariableRotation import get_active_rotation
        if get_active_rotation(self.task).run_current(self):
            return
        self._do_perform_default()

    def _do_perform_default(self):
        self.wait_down()
        self.do_everything()
        self.switch_next_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        from src.combat.VariableRotation import get_active_rotation
        from src.combat.StrictRotation import MUST, NO
        from src.char.BaseChar import SwitchPriority
        rot = get_active_rotation(self.task)
        if rot.is_active():
            priority = rot.priority_for(self.name)
            if priority == MUST:
                return SwitchPriority.MUST
            if priority == NO:
                return SwitchPriority.NO
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def perform_beat(self, beat):
        """Execute one strict-rotation beat (see src/combat/StrictRotation.py)."""
        from src.combat.StrictRotation import dash
        if beat.name in ('iuno_open1', 'iuno_open2'):
            # 2 / 4. skill
            self.click_resonance()
        elif beat.name == 'iuno_open3':
            # 6. echo
            self.click_echo()
        elif beat.name == 'iuno_loop1':
            # 12. skill, echo, dash, skill
            self.click_resonance()
            self.click_echo()
            dash(self)
            self.click_resonance()
        elif beat.name in ('iuno_burst', 'iuno_burst2'):
            # 8 / 14. (intro) jump-cancel, lib, skill, ba1234, skill, ba, ha, outro
            if beat.intro:
                self.wait_down()
            self._iuno_burst()
        else:  # defensive: unknown beat
            self.do_everything()

    def jump_cancel(self):
        """Jump-cancel Iuno's recovery/intro while the jump prompt is shown."""
        while self.task.find_feature("iuno_jump", box="box_extra_action", threshold=0.6):
            self.task.jump(after_sleep=0.1)

    def _iuno_burst(self):
        """Explicit burst: jump-cancel, lib, skill, ba1234, skill, ba, ha.

        The two skill casts are the point: each cast applies one of Iuno's buffs
        to the next character (Augusta), so both must fire before the outro. The
        generic do_everything returns right after Iuno's special heavy and does
        not guarantee the second skill cast, which left Augusta with only one of
        Iuno's two buffs.
        """
        from src.combat.StrictRotation import basic_attacks, heavy
        self.jump_cancel()
        # wait_if_cd_ready=0.5: the burst is a single lib attempt, so give a
        # finishing-cooldown liberation a brief chance to come up and fire instead
        # of skipping it for the whole burst. (If lib is genuinely not ready -- not
        # enough energy, or the lit icon is not being captured -- it still no-ops;
        # for the latter, use the WGC capture method, not BitBlt.)
        self.click_liberation(wait_if_cd_ready=0.5)
        # Iuno's skill has TWO charges, so both casts are available back-to-back --
        # no cooldown wait is needed between them. Each cast applies one of Iuno's
        # buffs, and the buff only registers once the cast resolves, so: (1) do NOT
        # animation-cancel around the skills -- a jump puts Iuno airborne and the
        # air-cast drops its buff; (2) use click_resonance (the registered cast the
        # other Iuno beats use) rather than a bare key send, and check both fired.
        # Both must land or the outro carries only one buff and Augusta comes in
        # under-buffed.
        cast1 = self.click_resonance(post_sleep=0.4)      # skill charge 1 -> buff 1
        basic_attacks(self, 4)                            # ba1234 (no cancel)
        # 2nd charge: a 2-charge skill shows a RECHARGE cooldown on its icon right
        # after charge 1, so resonance_available()/has_cd false-negatives and the
        # gated click_resonance skipped the 2nd cast on 18 of 19 bursts -- Iuno then
        # under-generated concerto AND only carried one of her two buffs to Augusta.
        # The charge is actually available, so FORCE the cast with a direct key send
        # rather than gating on the CD detection (harmless no-op if truly empty).
        self.send_resonance_key(post_sleep=0.4)           # skill charge 2 -> buff 2
        self.logger.info(f'Iuno burst skills: cast1={bool(cast1[0])} cast2=forced')
        basic_attacks(self, 1)                            # ba
        # ha: Iuno's special heavy (the extra-action prompt) applies a buff that
        # transfers on the outro. It is a 20s-cooldown move, so only chase it when
        # it is actually off cooldown; otherwise a plain heavy.
        #
        # Delegate the build+detect+fire to do_everything -- the battle-tested loop
        # that builds forte (echo/lib/skill/basics), jumps to go aerial (the
        # extra-action slot only shows the iuno_heavy prompt once she is airborne),
        # and fires the special heavy, looping until it lands or the window elapses.
        # The earlier bespoke window caught it only "sometimes" because 3s of
        # basics rarely built enough forte to light the prompt; do_everything
        # builds harder (echo/lib) and polls longer, so it lands far more often.
        # The two buff-skills are already cast above, so its only job here is the
        # heavy. force_complete keeps it going through the aerial jump.
        if self.time_elapsed_accounting_for_freeze(self.last_heavy) > 20:
            self.do_everything(time_out=4, force_complete=True)
        else:
            self.logger.info('Iuno burst: special heavy on cooldown, generic heavy')
            heavy(self)

    def do_everything(self, time_out=1.5, force_complete=False):
        if self.has_intro:
            time_out += 4
        start = time.time()
        last_action = "click"
        self.click_echo()
        c6_performed = False
        jumped = False
        while self.time_elapsed_accounting_for_freeze(start) < time_out:
            cycle_start = time.time()
            heavy_success = False
            while self.time_elapsed_accounting_for_freeze(
                    self.last_heavy) > 20 and self.task.find_feature("iuno_heavy",
                                                                     box="box_extra_action",
                                                                     threshold=0.6):
                # 特殊重击可用
                self.sleep(0.05)
                self.heavy_attack()
                self.sleep(0.05)
                heavy_success = True
            if heavy_success:
                self.last_heavy = time.time()
                # Settle so the special-heavy BUFF registers before the caller
                # acts on it. do_everything otherwise returns with only a 0.05s
                # tail, and the burst's outro top-off / grounding / swap (or the
                # reactive switch) then cancel the heavy before its buff lands.
                # 1.2s: 0.35s clipped the slam mid-animation and the buff was
                # still missing at 0.8s (the hit registers late in the ~1s+
                # animation), so give it clear margin.
                self.sleep(1.2)
                if not c6_performed and self.is_c6():
                    c6_performed = True
                    start = time.time()
                    time_out = 5
                    # 6命多打一轮
                    self.logger.debug('iuno c6 continue')
                else:
                    return True
            if not jumped and self.task.find_feature("iuno_jump", box="box_extra_action", threshold=0.6):
                # 可以跳 起跳
                while self.task.find_feature("iuno_jump", box="box_extra_action", threshold=0.6):
                    self.task.jump(after_sleep=0.1)
                time_out += 3
                jumped = True
                if self.has_intro or force_complete:
                    continue
                else:  # 没有intro, 切人取消后摇
                    return
            if self.time_elapsed_accounting_for_freeze(
                    self.last_liberation) > 20 and self.click_liberation(
                wait_if_cd_ready=0):
                # 开大招
                start = time.time()
                time_out = 3
            if last_action == "click":  # 左键和e轮流点击
                last_action = "resonance"
                self.send_resonance_key()
            else:
                last_action = "click"
                self.click()
            self.sleep(0.1 - (time.time() - cycle_start))

    def switch_next_char(self, *args, **kwargs):
        # Fire the special heavy on the way out if its prompt is up and off its
        # 20s cooldown: its buff must ride the outro to Augusta, and do_everything
        # can return before the prompt lights (the buff was then never applied).
        # One frame read when on cooldown, so cheap on every swap.
        if (self.time_elapsed_accounting_for_freeze(self.last_heavy) > 20
                and self.task.find_feature("iuno_heavy", box="box_extra_action",
                                           threshold=0.55)):
            self.heavy_attack()
            self.last_heavy = time.time()
            self.sleep(1.2)  # let the slam's hit register (same settle as do_everything)
            self.logger.info('Iuno: special heavy fired on switch-out')
        # Reactive-phase outro hardening: Iuno's outro carries her buffs to Augusta
        # but only fires at FULL concerto, and do_everything returns as soon as her
        # special heavy fires -- often short of full. When the scripted rotation is
        # NOT driving, build the ring the rest of the way with her high-yield
        # sources (echo/skill/lib via build_concerto), never early-bailing
        # (mandatory: Augusta must carry her buffs into the burst), then force the
        # outro. Bounded so it can't stall; below 0.6 con she just swaps and
        # accumulates for next time.
        from src.combat.VariableRotation import reactive_outro_topoff
        reactive_outro_topoff(self, kwargs, threshold=0.6, aggressive=True,
                              mandatory=True)
        return super().switch_next_char(*args, **kwargs)

    def on_combat_end(self, chars):
        self.switch_other_char()

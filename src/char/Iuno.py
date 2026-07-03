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
        """Press Space while the extra-action prompt shows.

        Lunar Cycle key-remap gotcha (user-verified): once Iuno is in Lunar
        Cycle, Space is NOT a jump -- it becomes Heavy Attack - Flux, the
        Half Moon <-> New Moon state toggle (and hold-LMB becomes Absolute
        Fullness at 100 concerto). The ``iuno_jump`` prompt in box_extra_action
        is that Flux prompt, so pressing Space here is the moon-state flip the
        rotation wants, not a movement jump."""
        while self.task.find_feature("iuno_jump", box="box_extra_action", threshold=0.6):
            self.task.jump(after_sleep=0.1)

    # Minimum time the mouse is KEPT DOWN for Absolute Fullness. Releasing on
    # "prompt cleared" was wrong: the prompt vanishes the instant the press
    # registers, so the hold collapsed into a <0.15s click (log: 'Absolute
    # Fullness held' -> swap 138ms later) -- an ordinary heavy, no buff.
    SPECIAL_HEAVY_HOLD = 1.0

    def _hold_special_heavy(self):
        """Iuno's special heavy (Absolute Fullness) is a HOLD, not a click: press
        and KEEP the mouse held for the full charge (SPECIAL_HEAVY_HOLD). A plain
        click fires an ordinary heavy and the buff never applies. The buff also
        requires FULL concerto -- callers gate on is_con_full() before invoking
        this. Do NOT release on the prompt clearing; it clears on press."""
        self.task.mouse_down()
        self.sleep(self.SPECIAL_HEAVY_HOLD)
        self.task.mouse_up()

    def _iuno_burst(self):
        """Burst per Iuno's kit contract (user-verified):

        - Arc Beyond the Edge (the resonance skill) is a CANCEL-INTO-ULTIMATE
          node: cast it, let its projectiles fire (the post_sleep gate), then the
          Ultimate cancels its endlag -- never clip the Arc before the
          projectiles are out.
        - Moonbow basics follow; P3 tolerates a swap after its first sub-hit, so
          the string needs no trailing settle.
        - The 2nd Arc charge is the swap-cancellable rotation FINISHER: the exit
          machinery that follows (top-off -> Absolute Fullness -> outro) is its
          cancel.
        - Absolute Fullness (the held special heavy at 100 concerto) fires at the
          EXIT, in switch_next_char: the outro swap is emitted DURING it -- it
          completes and its buff transfers regardless.
        - NO plain swap before the outro: she sheds her own stacks. Both skill
          buffs must also land before the outro (the buff registers only once the
          cast resolves), so no jumps around the skills -- an air-cast drops the
          buff.
        """
        from src.combat.StrictRotation import basic_attacks
        self.jump_cancel()
        # Arc #1: buff 1 applies; post_sleep is the projectiles-fired gate.
        cast1 = self.click_resonance(post_sleep=0.4)
        # Ultimate right after -- it is the kit's cancel for the Arc's endlag.
        # wait_if_cd_ready=0.5 gives a finishing-cooldown lib a brief chance to
        # come up; a genuinely unready lib no-ops and the rotation continues.
        self.click_liberation(wait_if_cd_ready=0.5)
        basic_attacks(self, 4)                            # Moonbow string
        # Arc #2 -> buff 2, the finisher. A 2-charge skill shows a RECHARGE
        # cooldown right after charge 1, so resonance_available() false-negatives
        # and a gated cast skipped it on 18 of 19 bursts -- FORCE the cast with a
        # direct key send (harmless no-op if truly empty).
        self.send_resonance_key(post_sleep=0.4)           # projectiles-fired gate
        self.logger.info(f'Iuno burst skills: cast1={bool(cast1[0])} cast2=forced')
        # Exit: the dwell/top-off brings concerto to 100, then switch_next_char
        # fires Absolute Fullness and emits the outro during it.

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
            while (self.time_elapsed_accounting_for_freeze(self.last_heavy) > 20
                   and self.task.find_feature("iuno_heavy", box="box_extra_action",
                                              threshold=0.6)
                   and self.is_con_full()):
                # special heavy: its buff requires FULL concerto and a HELD click
                # (a plain click fires an ordinary heavy and wastes the 20s CD).
                # Below full con we skip and keep building -- the switch-out
                # top-off finishes the ring and fires it there instead.
                self.sleep(0.05)
                self._hold_special_heavy()
                self.sleep(0.05)
                heavy_success = True
            if heavy_success:
                self.last_heavy = time.time()
                # Full Moon Domain is up: fixed 30s field timer that persists
                # with her off-field -- stamp it so the rotation can read the
                # live remaining.
                from src.combat.BuffTracker import get_buff_tracker, IUNO_DOMAIN
                get_buff_tracker(self.task).apply(IUNO_DOMAIN, source=self)
                # No settle: Absolute Fullness completes and its buff transfers
                # even when the swap lands during it (kit contract), so the
                # caller may switch out immediately.
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
        # Order matters: build concerto to FULL first (reactive top-off -- her
        # outro also needs it), because Absolute Fullness's buff ONLY applies at
        # 100 concerto. Then, at full, HOLD-fire it if its prompt is up and off
        # its 20s cooldown, and leave via the forced outro DURING the animation
        # (it completes and the buff transfers regardless). A plain swap before
        # the outro sheds her own stacks, so the mandatory top-off exists to turn
        # as many exits as possible into outros.
        from src.combat.VariableRotation import reactive_outro_topoff
        from src.combat.StrictRotation import confirm_con_full
        from src.combat.BuffTracker import (get_buff_tracker, IUNO_OUTRO,
                                            IUNO_DOMAIN)
        reactive_outro_topoff(self, kwargs, threshold=0.6, aggressive=True,
                              mandatory=True)
        tracker = get_buff_tracker(self.task)
        # Will this exit be an OUTRO? free_intro is only ever set on a
        # CONFIRMED-full ring (both top-off paths), so it implies con full and
        # skips a second read; otherwise double-read the ring ourselves --
        # confirm_con_full because her own Full Moon Domain sweeps ring-coloured
        # arcs through the concerto box, a single-frame full can be fake, and a
        # fake here would burn Absolute Fullness's 20s cooldown for no buff.
        will_outro = bool(kwargs.get('free_intro')) or confirm_con_full(self)
        if (will_outro
                and self.time_elapsed_accounting_for_freeze(self.last_heavy) > 20
                and self.task.find_feature("iuno_heavy", box="box_extra_action",
                                           threshold=0.55)):
            self._hold_special_heavy()
            self.last_heavy = time.time()
            tracker.apply(IUNO_DOMAIN, source=self)  # 30s field, persists off-field
            # NO settle: the outro swap is emitted DURING Absolute Fullness --
            # it completes and its buff transfers regardless (kit contract).
            self.logger.info('Iuno: Absolute Fullness held at 100 concerto; '
                             'outroing during it')
        # She is never a bound receiver today, so this is a no-op safety net --
        # but keep the swap-out hook symmetric across the team.
        tracker.on_char_switch_out(self.char_name)
        if will_outro:
            # 50% Heavy-Attack amp rides this swap to the incoming character.
            # The RECEIVER binds itself (Augusta._buffed_window) so the tracker
            # can end the buff early if the receiver is switched off; until
            # then remaining() counts down its 15s window.
            tracker.apply(IUNO_OUTRO, source=self)
        return super().switch_next_char(*args, **kwargs)

    def on_combat_end(self, chars):
        self.switch_other_char()

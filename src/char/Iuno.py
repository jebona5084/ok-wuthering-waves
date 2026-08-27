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
        # LIB-CD-ALIGNED amp bank (user: Augusta must have both buffs when her
        # lib cd ends; measured: the 15s amp cannot survive the 25s cd, so the
        # bank must land in the cd's LAST stretch). Iuno claims the field --
        # plain exits AND ShoreKeeper's outro hand-off (Augusta cedes it while
        # the bank is pending) -- exactly when the bank window is open. She
        # never claims Augusta's own intro exits (SK's refresh owns that slot,
        # first in the window) and never claims within 4s of leaving (no
        # self-bounce). Falls back to the old amp-down/SK-field-live rule when
        # there is no Augusta on the team.
        try:
            from src.char.Augusta import amp_bank_window_open
            from src.char.ShoreKeeper import ShoreKeeper
            from src.combat.BuffTracker import (get_buff_tracker,
                                                IUNO_OUTRO, SK_LIBERATION)
            t, window_open = amp_bank_window_open(self.task)
            no_bounce = self.time_elapsed_accounting_for_freeze(
                self.last_switch_time) > 4
            if t is not None:
                if (window_open and no_bounce
                        and (not has_intro
                             or isinstance(current_char, ShoreKeeper))):
                    return SwitchPriority.MUST
            elif not has_intro:
                tracker = get_buff_tracker(self.task)
                if (tracker.has(IUNO_OUTRO)
                        and tracker.remaining(IUNO_OUTRO) <= 0
                        and tracker.remaining(SK_LIBERATION) > 8
                        and no_bounce):
                    return SwitchPriority.MUST
        except Exception as e:
            self.logger.debug(f'Iuno bank-window claim failed: {e}')
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def perform_beat(self, beat):
        """Execute one strict-rotation beat (see src/combat/StrictRotation.py)."""
        from src.combat.StrictRotation import dash
        if beat.name in ('iuno_open1', 'iuno_open2'):
            # 2 / 4. skill, then Space if the Flux prompt lit (user: 'iuno
            # should press spacbar if the skill is available' -- jump_cancel
            # presses Space only while the iuno_jump prompt shows, so this is
            # a no-op when it is not available).
            self.click_resonance()
            self.jump_cancel(wait=0.8)
        elif beat.name == 'iuno_open3':
            # 6. echo, then Space if the Flux prompt lit
            self.click_echo()
            self.jump_cancel(wait=0.8)
        elif beat.name == 'iuno_loop1':
            # 12. skill, echo, dash, skill -- Space after each cast when lit.
            # (The burst beats stay untouched: they already Flux at entry, and
            # the kit bans jumps AROUND the Arc casts -- an air-cast drops the
            # skill buff.)
            self.click_resonance()
            self.jump_cancel(wait=0.8)
            self.click_echo()
            dash(self)
            self.click_resonance()
            self.jump_cancel(wait=0.8)
        elif beat.name in ('iuno_burst', 'iuno_burst2'):
            # 8 / 14. (intro) jump-cancel, lib, skill, ba1234, skill, ba, ha, outro
            if beat.intro:
                self.wait_down()
            self._iuno_burst()
        else:  # defensive: unknown beat
            self.do_everything()

    def jump_cancel(self, wait=0.0):
        """Press Space while the extra-action prompt shows.

        Lunar Cycle key-remap gotcha (user-verified): once Iuno is in Lunar
        Cycle, Space is NOT a jump -- it becomes Heavy Attack - Flux, the
        Half Moon <-> New Moon state toggle (and hold-LMB becomes Absolute
        Fullness at 100 concerto). The ``iuno_jump`` prompt in box_extra_action
        is that Flux prompt, so pressing Space here is the moon-state flip the
        rotation wants, not a movement jump.

        ``wait``: poll up to this long for the prompt to LIGHT before giving
        up (user: 'iuno not using spacebar skill when its available' -- the
        prompt lights a beat AFTER a cast resolves, so an instantaneous check
        right after click_resonance missed it every time). Returns True if
        Space was pressed."""
        end = time.time() + wait
        pressed = False
        while True:
            while self.task.find_feature("iuno_jump", box="box_extra_action",
                                         threshold=0.6):
                self.task.jump(after_sleep=0.1)
                pressed = True
            if pressed or time.time() >= end:
                return pressed
            self.task.next_frame()

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

    def _try_absolute_fullness(self):
        """Hold-fire Absolute Fullness if its full gate passes; True when held.

        Gate: off its own ~20s cooldown, the iuno_heavy prompt visible, and
        CONFIRMED full concerto (double-read -- her own Full Moon Domain sweeps
        ring-coloured arcs through the concerto box, and a fake full here would
        burn the 20s cooldown for no buff). The prompt also requires an Arc
        CHARGE (user-verified: 'iuno didn't heavy attack because her skill was
        on cooldown'), which is why callers care about WHEN they try this
        relative to Arc casts."""
        from src.combat.StrictRotation import confirm_con_full
        if self.time_elapsed_accounting_for_freeze(self.last_heavy) <= 20:
            return False
        if not self.task.find_feature("iuno_heavy", box="box_extra_action",
                                      threshold=0.55):
            return False
        if not confirm_con_full(self):
            return False
        self._hold_special_heavy()
        self.last_heavy = time.time()
        # Full Moon Domain is up: fixed 30s field timer that persists off-field.
        from src.combat.BuffTracker import get_buff_tracker, IUNO_DOMAIN
        get_buff_tracker(self.task).apply(IUNO_DOMAIN, source=self)
        self.logger.info('Iuno: Absolute Fullness held at 100 concerto')
        return True

    # Longest the exit gate will wait for an Arc charge to come back so the
    # heavy prompt returns; the actual wait is bounded by the CD read, this cap
    # only protects against a misread CD stalling the rotation.
    HEAVY_PROMPT_WAIT_MAX = 9.0

    def _wait_for_heavy_prompt(self, max_wait=HEAVY_PROMPT_WAIT_MAX):
        """Wait out the Arc recharge until the Absolute Fullness prompt returns.

        The AF prompt needs an Arc charge; the burst force-casts BOTH charges,
        so at the full-concerto exit the prompt is gone for the tail of the
        ~8.6s recharge (video 678adb85: cd read 8.1 at the exit, AF silently
        skipped, Augusta's badge stayed 0). The buff is cycle-critical, so when
        AF is due and only the charge is missing, waiting the recharge out is
        cheaper than losing the amp for a whole 20s+ cycle. Bounded by the
        actual CD read (+grace) and MAX; fills the wait with plain clicks
        (normal attacks -- the ring is already full, nothing is wasted)."""
        cd = 0.0
        try:
            cd = max(0.0, float(self.task.get_cd('resonance')))
        except Exception as e:
            self.logger.debug(f'Iuno: resonance cd read failed: {e}')
        # cd==0 with the prompt missing is a transient (cd OCR miss / prompt
        # redraw): give it a short grace instead of the full cap.
        wait = min(cd + 0.8, max_wait) if cd > 0 else 1.0
        self.logger.info(f'Iuno: heavy prompt missing (Arc recharging, cd~{cd:.1f}s); '
                         f'waiting up to {wait:.1f}s for the charge')
        return self.task.wait_until(
            lambda: self.task.find_feature("iuno_heavy", box="box_extra_action",
                                           threshold=0.55),
            post_action=self.click, time_out=wait)

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
        # Absolute Fullness needs an Arc CHARGE (user-verified: skipped when
        # 'her skill was on cooldown') -- and the forced Arc #2 below spends the
        # LAST one, locking AF for the ~8.6s recharge exactly when the exit
        # wants to fire it (video 678adb85). If the Moonbow string already
        # filled the ring, hold AF NOW while charge #2 is still in hand; the
        # exit gate remains the fallback (it waits out the recharge).
        self._try_absolute_fullness()
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
            if (self.time_elapsed_accounting_for_freeze(
                    self.last_liberation) > 20
                    and self._domain_recast_ok()
                    and self.click_liberation(wait_if_cd_ready=0)):
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

    def _domain_recast_ok(self):
        """Skip the ~4.5s Full Moon Domain recast when the amp bank is tight.

        Measured (log 3a0c1e77): the domain cast ate ~4.5s of her 10.7s bank
        visit, pushing the amp outro past Augusta's lib-ready. In the bank
        window's tail the amp comes FIRST; the domain (30s field, not read by
        the burst gate) goes up on the next, unhurried visit."""
        try:
            from src.char.Augusta import amp_bank_window_open
            t, amp_short = amp_bank_window_open(self.task)
            if t is None:
                return True
            return not (amp_short and t <= 8.0)
        except Exception:
            return True

    def switch_next_char(self, *args, **kwargs):
        # Order matters: build concerto to FULL first (reactive top-off -- her
        # outro also needs it), because Absolute Fullness's buff ONLY applies at
        # 100 concerto. Then, at full, HOLD-fire it if its prompt is up and off
        # its 20s cooldown, and leave via the forced outro DURING the animation
        # (it completes and the buff transfers regardless). A plain swap before
        # the outro sheds her own stacks, so the mandatory top-off exists to turn
        # as many exits as possible into outros.
        from src.combat.VariableRotation import (reactive_outro_topoff,
                                                 get_active_rotation)
        from src.combat.StrictRotation import confirm_con_full, build_concerto
        from src.combat.BuffTracker import get_buff_tracker, IUNO_OUTRO
        tracker = get_buff_tracker(self.task)
        # Bank-window-gated build-to-full: ONLY when the amp must be banked for
        # the upcoming burst does this exit build from any level (threshold 0).
        # Outside the window the 0.6 threshold stands -- a full ring banked
        # EARLY auto-outros (the engine forces has_intro at con==1) and the
        # 15s amp then dies before the ~25s lib cd ends, which is exactly the
        # measured failure (log 3a0c1e77: amp dead at both lib-ready moments).
        t = None
        amp_short = False
        try:
            from src.char.Augusta import amp_bank_window_open
            t, amp_short = amp_bank_window_open(self.task)
        except Exception as e:
            self.logger.debug(f'Iuno bank window read failed: {e}')
        threshold = 0.6
        if ((t is not None and t <= 14.0 and amp_short)
                or (t is None and tracker.has(IUNO_OUTRO)
                    and tracker.remaining(IUNO_OUTRO) <= 0)):
            threshold = 0.0
        reactive_outro_topoff(self, kwargs, threshold=threshold, aggressive=True,
                              mandatory=True)
        # HOLD-AND-FINISH (reactive only): time the banking outro into the
        # LAST seconds of Augusta's lib cd. Full-but-early -> hold the field
        # with filler (an early outro wastes the amp); not-full -> keep
        # building (kills the measured 0.94-exit-without-outro miss). Bounded
        # so a stuck read can never wedge the fight.
        IUNO_OUTRO_LEAD = 4.0   # amp needs >=4s at the majesty gate (~t+7)
        IUNO_HOLD_MAX = 12.0
        if (amp_short and t is not None
                and not get_active_rotation(self.task).is_active()):
            hold_start = time.time()
            from src.char.Augusta import augusta_lib_remaining
            while time.time() - hold_start < IUNO_HOLD_MAX:
                full = confirm_con_full(self)
                remaining = augusta_lib_remaining(self.task)
                if full and (remaining is None or remaining <= IUNO_OUTRO_LEAD):
                    break
                if not full:
                    build_concerto(self)
                else:
                    # idle at full: cash a lit Flux prompt (user: 'iuno not
                    # using spacebar skill when its available'), else filler
                    if not self.jump_cancel():
                        self.click()
                        self.sleep(0.1)
            if confirm_con_full(self):
                kwargs['free_intro'] = True
        # Will this exit be an OUTRO? free_intro is only ever set on a
        # CONFIRMED-full ring (both top-off paths), so it implies con full and
        # skips a second read; otherwise double-read the ring ourselves --
        # confirm_con_full because her own Full Moon Domain sweeps ring-coloured
        # arcs through the concerto box, a single-frame full can be fake, and a
        # fake here would burn Absolute Fullness's 20s cooldown for no buff.
        will_outro = bool(kwargs.get('free_intro')) or confirm_con_full(self)
        if (will_outro
                and self.time_elapsed_accounting_for_freeze(self.last_heavy) > 20):
            if not self.task.find_feature("iuno_heavy", box="box_extra_action",
                                          threshold=0.55):
                # AF is due on a full ring but its prompt is gone -- the Arc
                # recharge is the usual culprit (the burst spends both charges).
                # Wait it out rather than silently losing the amp.
                self._wait_for_heavy_prompt()
            if self._try_absolute_fullness():
                # NO settle: the outro swap is emitted DURING Absolute Fullness
                # -- it completes and its buff transfers regardless (kit
                # contract). (last_heavy + domain stamp handled in the helper.)
                self.logger.info('Iuno: outroing during Absolute Fullness')
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

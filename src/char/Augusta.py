import re
import time

from src.char.BaseChar import BaseChar

"""
    几个长派生帧动作的切人时间阈值,改小可以减少站场时间
    初始为 3
"""
switch_time = 3


class Augusta(BaseChar):
    # Griffin (Enhanced Resonance Skill) is a 3-hit combo; it builds Majesty energy.
    ENHANCED_SKILL_COUNT = 3
    # The 10-count badge is IUNO'S BUFF on Augusta (climbs 1..10 during her
    # buffed window) -- NOT Majesty. Majesty is a separate resource (2 stacks)
    # that lights the 2nd-liberation icon (check_majesty / Augusta_lib2). Badge
    # digit box in 3840x2160 ref px (pinned from an in-game hover at normalized
    # (0.501, 0.840)); widened left so "10" fits.
    IUNO_BUFF_BOX = (1886, 1789, 1950, 1839)
    IUNO_BUFF_TARGET = 10
    # Augusta's big burst must ride BOTH support buffs. SK's outro buff has no
    # badge to OCR, so it is tracked by recency of her last con-full exit
    # (ShoreKeeper.outrotime). The buff lasts ~30s of GAME time and the elapsed
    # here is freeze-adjusted (same clock), so use nearly the full duration: a
    # 25s window discarded a burst-ready Augusta (0.95 con, lib2 lit) at ~27s
    # elapsed while the buff was visibly still active (log 02:16:37), and the
    # rotation then ping-ponged as the two buff windows never overlapped.
    SK_BUFF_WINDOW = 29
    # Iuno's buff after her outro: the engine's extended-intro FIELD window is
    # 14s, but the buff itself outlasts it a little; 15s with margin. Recency is
    # the PRIMARY detector: at 1 stack her badge shows NO digit, so the OCR
    # reads 0 right when the buff is freshest (log f8c2363e).
    IUNO_BUFF_WINDOW = 15
    # Escape hatch: never hold a lit 2nd lib hostage forever -- if the buffs are
    # still not both up after this long, burst anyway rather than waste it.
    MAJESTY_HOLD_MAX = 40
    _majesty_wait_start = -1.0

    def do_perform(self):
        from src.combat.VariableRotation import get_active_rotation
        if get_active_rotation(self.task).run_current(self):
            return
        self._do_perform_default()

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
        # Reactive phase (user request): Augusta CLAIMS ShoreKeeper's con-full
        # outro, so SK's amp + recovery butterflies land directly on her for the
        # burst. Iuno's outro also defaults to Augusta, and SK claims Augusta's
        # con-full exit -- the cycle is Augusta -> SK -> Augusta with Iuno feeding
        # in between.
        from src.char.ShoreKeeper import ShoreKeeper
        if has_intro and isinstance(current_char, ShoreKeeper):
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def perform_beat(self, beat):
        """Execute one strict-rotation beat (see src/combat/StrictRotation.py)."""
        from src.combat.StrictRotation import heavy
        if beat.intro:
            self.wait_down()
        if beat.name == 'aug_open':
            # 1. skill -- hold briefly after the cast: the hand-off (jump-cancel +
            # swap) otherwise lands ~0.2s after the E press and cancels the cast
            # before its effect registers (footage showed the opening E never
            # landing).
            self.click_resonance(post_sleep=0.5)
        elif beat.name == 'aug_open2':
            # 5. ha
            self._heavy_or_prowess()
        elif beat.name == 'aug_loop1':
            # 11. intro, ha
            self._heavy_or_prowess()
        elif beat.name == 'aug_loop2':
            # 13. skill, ha
            self.click_resonance()
            self._heavy_or_prowess()
        elif beat.name == 'aug_burst':
            # 9. intro, ha, lib (griffin), skill, ha, 2nd lib, echo, outro
            self._augusta_burst(with_basics=False)
        elif beat.name == 'aug_burst2':
            # 15. ha, lib (griffin), skill, ha, 2nd lib, ba123, ha, echo, outro
            self._augusta_burst(with_basics=True)
        else:  # defensive: unknown beat -> conservative damage
            self.click_resonance()
            heavy(self)

    def _heavy_or_prowess(self, cancel=False):
        from src.combat.StrictRotation import heavy
        if self.check_prowess():
            self.perform_prowess()
        else:
            heavy(self, cancel=cancel)

    def iuno_buff_stacks(self):
        """OCR Iuno's buff-stack badge on Augusta (0 if no digit / can't be read).

        The digit is white on the badge, so isolate white text first (blacks out
        the icon/fill and leaves the number). At the lowest the badge shows no
        digit -> reads 0.
        """
        from src.task.BaseWWTask import isolate_white_text_to_black
        box = self.task.box_of_screen_scaled(
            3840, 2160, *self.IUNO_BUFF_BOX, name='iuno_buff', hcenter=True)
        self.task.draw_boxes(box.name, box)
        stacks = 0
        for t in self.task.ocr(box=box, match=re.compile(r'\d+'),
                               frame_processor=isolate_white_text_to_black):
            try:
                stacks = max(stacks, int(re.sub(r'\D', '', t.name)))
            except (ValueError, TypeError):
                continue
        self.logger.debug(f'Augusta iuno_buff_stacks = {stacks}')
        return stacks

    def _build_majesty(self):
        """One action to push Majesty toward max while holding for max stacks.

        The Enhanced Resonance Skill (griffin) is Augusta's main Majesty builder,
        so prefer it when it is off cooldown; then the forte/prowess heavy, else a
        basic attack. (A plain basic alone barely moves the badge, which is why
        she stalled short of the target.)"""
        if self.resonance_available():
            self.click_resonance()   # griffin / enhanced skill -> Majesty
            return
        if self.is_forte_full() and self.heavy_click_forte(self.is_forte_full):
            return
        if self.check_prowess() and self.perform_prowess():
            return
        self.click()

    def _build_and_cast_majesty(self, majesty_time_out=12):
        """Build Iuno's buff to max, then KEEP ROTATING on field until the 2nd
        liberation (Majesty, 2 stacks -> Augusta_lib2 icon) is ready, then cast.

        The 10-count badge is IUNO'S BUFF -- it maxing does not mean the 2nd lib
        is ready, because Majesty is its own resource. Previously this polled
        check_majesty passively for only 1.5s after the badge hit 10 and then
        SKIPPED the 2nd lib and switched out, wasting the fully-buffed window.
        Now she stays and keeps building (enhanced skill / prowess / basics) via
        _build_majesty until the lib2 icon lights -- bounded by majesty_time_out,
        sized to fit inside her ~14s buffed dwell window. Returns True if the
        2nd lib was cast."""
        if self.iuno_buff_stacks() < self.IUNO_BUFF_TARGET:
            self.logger.info(f"Augusta: building Iuno's buff to {self.IUNO_BUFF_TARGET} "
                             f"before the 2nd lib")
            self.task.wait_until(
                lambda: self.iuno_buff_stacks() >= self.IUNO_BUFF_TARGET,
                post_action=self._build_majesty, time_out=4)
        if self.task.wait_until(self.check_majesty, post_action=self._build_majesty,
                                time_out=majesty_time_out):
            # Spinslash 2 -> 2nd Ultimate: with the lib2 icon confirmed lit, fire
            # the spinslash (its hit lands within the call) and chain the ult
            # right after -- the ult is the kit's cancel for the spinslash endlag.
            self._heavy_or_prowess()
            return self.perform_majesty()
        self.logger.info('Augusta: lib2 (majesty, 2 stacks) never lit within '
                         f'{majesty_time_out}s, skipping 2nd lib')
        return False

    def _augusta_burst(self, with_basics):
        # Augusta's kit contract (user-verified; community-derived timings -- if a
        # game patch retunes endlag and Majesty stacks start dropping, re-verify
        # these cancel points first):
        # - CHAIN-cancel, never jump-cancel: Spinslash 1's endlag is cancelled by
        #   the BASE RESONANCE SKILL, Spinslash 2's by the SECOND ULTIMATE. The
        #   rule is "wait until the hit lands, then chain" -- each heavy call
        #   returns after its press/hit, and the next ability is sent right after.
        #   No jumps anywhere in her rotation (a jump can clip the damage; the
        #   chain ability IS the kit's cancel).
        # - NO character swap between Intro and the 2nd Ultimate: she plays under
        #   an Amplify outro buff that a swap would end, and ~half her damage sits
        #   before the 2nd Ultimate. The coordinator already keeps her on field
        #   for the whole beat; nothing here may switch out early.
        # - The ONE clean exit: 2nd Ultimate sequence -> one False Sovereign echo
        #   -> Outro swap (the swap right after is the echo's cancel).
        # Libs stay ICON-gated: lib 1 on liberation_available() (Augusta_lib1),
        # lib 2 on check_majesty() (Augusta_lib2) -- casting unlit was the old
        # 'not in animation' failure.
        from src.combat.StrictRotation import basic_attacks
        self._heavy_or_prowess()                 # Spinslash 1 (hit lands in-call)
        self.click_resonance()                   # base skill -- cancels SS1 endlag
        # 1st Resonance Liberation -- fire when its icon (Augusta_lib1) is lit.
        if self.liberation_available():
            self.task.wait_until(lambda: not self.liberation_available(),
                                 post_action=self.send_liberation_key, time_out=2)
            self.record_liberation_use()
        # Griffin = Enhanced Resonance Skill (3 hits). Builds Majesty, which lights
        # the 2nd-liberation icon (Augusta_lib2).
        for _ in range(self.ENHANCED_SKILL_COUNT):
            self.click_resonance()               # griffin hit (x3)
        if with_basics:
            # buffed filler goes BEFORE the payoff -- nothing trails the 2nd ult.
            basic_attacks(self, 3, forte_check=self.check_prowess)  # ba123
        # 2nd Ultimate ("majesty"): build Iuno's buff to 10, keep rotating until
        # the lib2 icon lights, then Spinslash 2 -> 2nd ult (the ult cancels the
        # spinslash's endlag) -- see _build_and_cast_majesty.
        self._build_and_cast_majesty()
        self.send_echo_key()                     # False Sovereign echo -- the outro
        #                                          swap right after is its cancel

    def _sk_outro_elapsed(self):
        """Freeze-adjusted seconds since ShoreKeeper's last con-full exit (which
        stamps her ``outrotime``); inf when she never outro'd."""
        from src.char.ShoreKeeper import ShoreKeeper
        for char in self.task.chars:
            if isinstance(char, ShoreKeeper):
                return char.time_elapsed_accounting_for_freeze(char.outrotime)
        return float('inf')

    def _iuno_outro_elapsed(self):
        """Freeze-adjusted seconds since Iuno's last outro (the engine stamps
        ``last_outro_time`` on her con-full exits); inf when she never outro'd."""
        from src.char.Iuno import Iuno
        for char in self.task.chars:
            if isinstance(char, Iuno):
                return char.time_elapsed_accounting_for_freeze(char.last_outro_time)
        return float('inf')

    def _sk_buff_active(self):
        """ShoreKeeper's outro buff is (approximately) live."""
        return self._sk_outro_elapsed() < self.SK_BUFF_WINDOW

    def _iuno_buff_active(self):
        """Iuno's buff is on Augusta.

        PRIMARY: outro recency. The badge OCR is only a confirming fallback: at
        1 stack the badge shows no digit and reads 0, exactly when the buff is
        freshest, so it must not be the gate."""
        if self._iuno_outro_elapsed() < self.IUNO_BUFF_WINDOW:
            return True
        return self.iuno_buff_stacks() >= 1

    def _team_buffs_ready(self):
        """Whether Augusta carries BOTH support buffs for her big burst.

        User requirement: she must always have SK's and Iuno's buffs before the
        majesty burst. Both are tracked by outro recency (Iuno's badge OCR only
        confirms -- it reads 0 at 1 stack). Bounded by MAJESTY_HOLD_MAX so a
        broken tracker or a dead support can never hold a lit 2nd lib hostage
        forever."""
        iuno_ok = self._iuno_buff_active()
        sk_ok = self._sk_buff_active()
        detail = (f'iuno={iuno_ok} ({self._iuno_outro_elapsed():.0f}s ago, win '
                  f'{self.IUNO_BUFF_WINDOW}) sk={sk_ok} ({self._sk_outro_elapsed():.0f}s '
                  f'ago, win {self.SK_BUFF_WINDOW})')
        if iuno_ok and sk_ok:
            self._majesty_wait_start = -1.0
            return True
        now = time.time()
        if self._majesty_wait_start < 0:
            self._majesty_wait_start = now
            self.logger.info(f'Augusta: holding 2nd lib for team buffs ({detail})')
        else:
            self.logger.debug(f'Augusta: still holding 2nd lib ({detail})')
        if now - self._majesty_wait_start > self.MAJESTY_HOLD_MAX:
            self.logger.info(f'Augusta: team buffs still missing after '
                             f'{self.MAJESTY_HOLD_MAX}s ({detail}), bursting anyway')
            self._majesty_wait_start = -1.0
            return True
        return False

    def _do_perform_default(self):
        time_out = switch_time
        if self.has_intro:
            self.continues_normal_attack(1.13)
            if self.has_sub_dps_intro and self.check_outro() in {'char_iuno'}:
                time_out = 14
        if self.flying():
            self.wait_down()
        start = time.time()
        timeout = lambda: time.time() - start < time_out + 3
        while timeout():
            self.cycle_start()
            if self.check_majesty():
                if not self._team_buffs_ready():
                    # hold the big burst until BOTH support buffs are on her --
                    # switch out so the cycle brings Iuno/SK back to apply them.
                    return self.switch_next_char()
                self.logger.debug('Augusta majesty icon lit; building to target')
                if self._build_and_cast_majesty():
                    self.send_echo_key()
                    return self.switch_next_char()
            if self.flying():
                self.shorekeeper_auto_dodge()
            if self.check_prowess() and self.perform_prowess():
                if time.time() - start > time_out:
                    return self.switch_next_char()
            if self.resonance_available():
                self.logger.debug('Augusta performs single resonance')
                now = time.time()
                self.click_resonance()
                self.logger.debug(f'time = {time.time() - now}')
                if time.time() - now < 1.4:
                    if self.flying():
                        continue
                    if self.task.wait_until(self.check_prowess, time_out=1) and self.perform_prowess():
                        if time.time() - start > time_out and not self.flying():
                            return self.switch_next_char()
                else:
                    if self.check_majesty():
                        if not self._team_buffs_ready():
                            return self.switch_next_char()  # burst waits for buffs
                        self.wait_down()
                        if self._build_and_cast_majesty():
                            self.send_echo_key()
                        return self.switch_next_char()
            if self.liberation_available():
                self.logger.debug('Augusta performs single liberation')
                if self.task.wait_until(lambda: not self.liberation_available(), post_action=self.send_liberation_key,
                                        time_out=2):
                    self.record_liberation_use()
                    # Majesty block (reactive-phase fix): the old code switched out
                    # RIGHT HERE on every normal (time_out < 14) visit -- the lib
                    # landed and Augusta left before a single Griffin cast, so her
                    # main Majesty builder trickled in at ~1 enhanced skill per 3s
                    # visit, interleaved with Iuno/SK field time, and lib2 took
                    # whole team cycles to light. Do what the scripted aug_burst
                    # does instead: chain the Griffin hits NOW, in the same visit
                    # as the lib that enabled them.
                    for _ in range(self.ENHANCED_SKILL_COUNT):
                        self.click_resonance()           # griffin x3 -> Majesty
                    self._heavy_or_prowess()             # prowess/forte: Majesty + con
                    if self.check_majesty():
                        if not self._team_buffs_ready():
                            # hold the big burst for both support buffs; the exit
                            # below is outro-hardened, so SK claims the intro and
                            # the cycle brings the buffs back onto her.
                            return self.switch_next_char()
                        if self._build_and_cast_majesty():
                            self.send_echo_key()
                        return self.switch_next_char()
                    if time_out < 14:
                        return self.switch_next_char()
                    # inside Iuno's 14s buffed window with lib2 not lit yet: stay
                    # on field -- the loop keeps building (prowess / griffin) and
                    # the majesty check at the top fires the burst when it lights.
            self.click()
            self.cycle_sleep()
        self.send_echo_key()
        self.switch_next_char()

    def perform_prowess(self):
        self.logger.debug('Augusta performs prowess')
        if not self.heavy_click_forte(self.check_prowess):
            return False
        self.continues_normal_attack(0.3)
        return True

    def perform_majesty(self, time_out=0.6, wait_down=False):
        self.task.send_key_down(self.get_liberation_key())
        self.task.in_liberation = True
        if wait_down:
            time_out = 0.2
            self.task.wait_until(lambda: not self.task.in_team()[0] or not self.flying(), time_out=2)
        self.task.wait_until(lambda: not self.task.in_team()[0], time_out=time_out)
        start = time.time()
        self.task.send_key_up(self.get_liberation_key())
        if self.task.in_team()[0]:
            self.logger.debug('Augusta performs majesty failed: not in animation')
            self.task.in_liberation = False
            return False
        self.task.wait_until(lambda: self.task.in_team()[0], post_action=self.click, time_out=10)
        self.add_freeze_duration(start, time.time() - start)
        self.logger.info(f'click_liberation end {time.time() - start}')

        return True

    def check_ascendancy(self):
        return False

    def liberation_available(self, check_color=True):
        return self.current_liberation() > 0 and bool(self.task.find_one('Augusta_lib1', threshold=0.5))

    def check_majesty(self):
        return self.current_liberation() > 0 and bool(self.task.find_one('Augusta_lib2', threshold=0.5))

    def check_prowess(self):
        long_inner_box = 'target_enemy_long_inner'
        if self.task.find_one(long_inner_box, threshold=0.8):
            return True

    def resonance_available(self):
        return not self.has_cd('resonance')

    def shorekeeper_auto_dodge(self):
        from src.char.ShoreKeeper import ShoreKeeper
        for i, char in enumerate(self.task.chars):
            if isinstance(char, ShoreKeeper):
                return char.auto_dodge(condition=self.flying)

    # Reactive-phase outro top-off knobs. Her outro is CYCLE-CRITICAL, same as
    # the supports': (1) the kit restores +1 Majesty when the character she
    # outros into performs their own outro -- with plain-swap exits that chain
    # never runs and Majesty rebuilds only from griffin casts; (2) an outro exit
    # hands SK the intro her MUST-claim keys off, so the Augusta -> SK -> Augusta
    # buff cycle actually turns. So she gets the supports' mandatory-style budget.
    OUTRO_TOPOFF_THRESHOLD = 0.6
    OUTRO_TOPOFF_BUDGET = 4.0

    def _build_concerto_majesty(self):
        """One concerto-building action that is SAFE for Augusta.

        The shared ``build_concerto`` casts LIBERATION first -- for Augusta that
        would burn a lit lib1 (or the lit lib2 she is holding for team buffs)
        just to fill the ring; ``reactive_outro_topoff``'s own docstring bans
        exactly that for a main DPS, which is why she was left with 0.8s of
        plain basics before. Her echo (False Sovereign) stays reserved too --
        it is the burst finisher. Build with: enhanced skill / griffin first
        (double-dips: concerto AND Majesty energy), then prowess, then forte,
        else a basic attack.
        """
        if self.resonance_available():
            self.click_resonance()
            return
        if self.check_prowess() and self.perform_prowess():
            return
        if self.heavy_click_forte(self.is_forte_full):
            return
        self.click()

    def _reactive_outro_topoff(self, kwargs):
        """Outro-harden Augusta's REACTIVE-phase exits (mutates ``kwargs``).

        No-op while the scripted rotation drives (it tops off before its own
        hand-offs). The generic ``reactive_outro_topoff(self, kwargs)`` this
        replaces gave her only 0.8s of plain basics above 0.7 con -- SK's file
        logs that exact top-off leaving at 0.89/0.74 con -- so most of her exits
        were PLAIN swaps: no outro, no +1 Majesty from the partner-outro chain,
        and no intro for SK's MUST-claim. Build with her real (lib-safe) sources
        instead and force the outro path on a confirmed-full ring.
        """
        from src.combat.VariableRotation import get_active_rotation
        from src.combat.StrictRotation import confirm_con_full, OUTRO_SWAP_SETTLE
        if get_active_rotation(self.task).is_active():
            return kwargs
        con = self.get_current_con()
        if self.OUTRO_TOPOFF_THRESHOLD <= con < 1:
            start = time.time()
            while time.time() - start < self.OUTRO_TOPOFF_BUDGET:
                if confirm_con_full(self):
                    break
                self._build_concerto_majesty()
        if confirm_con_full(self):
            if self.flying():
                self.wait_down()
            # settle the last action so the swap reads as a clean outro (same
            # rationale as the scripted hand-off's OUTRO_SWAP_SETTLE).
            self.sleep(OUTRO_SWAP_SETTLE)
            kwargs['free_intro'] = True
        return kwargs

    def switch_next_char(self, *args, **kwargs):
        # Reactive-phase outro hardening: finish a near-full ring with her
        # lib-safe builders before swapping so the swap outros (Majesty chain +
        # SK's claim) instead of wasting it. No-op while the scripted rotation
        # drives (it tops off before its own outros).
        self._reactive_outro_topoff(kwargs)
        return super().switch_next_char(*args, **kwargs)

    def on_combat_end(self, chars):
        next_char = str((self.index + 1) % len(chars) + 1)
        self.logger.debug(f'Augusta on_combat_end {self.index} switch next char: {next_char}')
        self.task.send_key(next_char)

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
            return self.perform_majesty()
        self.logger.info('Augusta: lib2 (majesty, 2 stacks) never lit within '
                         f'{majesty_time_out}s, skipping 2nd lib')
        return False

    def _augusta_burst(self, with_basics):
        # Augusta's kit (per the reference + guide): Resonance Skill -> 1st Resonance
        # Liberation -> Griffin (Enhanced Resonance Skill, a 3-hit combo that builds
        # Majesty energy) -> 2nd Resonance Liberation (Majesty-empowered) -> basics.
        # The reference gates each lib on its ICON: lib 1 on liberation_available()
        # (Augusta_lib1), lib 2 on check_majesty() (Augusta_lib2). Calling
        # perform_majesty WITHOUT that gate is what caused 'not in animation' -- lib2
        # was not lit yet. So follow the reference and gate on the icons.
        from src.combat.StrictRotation import heavy, basic_attacks, aggressive_cancel_enabled
        agg = aggressive_cancel_enabled(self.task)
        # First heavy flows straight into the plunge skill -- do NOT cancel it, a
        # jump here would disturb that transition.
        self._heavy_or_prowess()                 # ha (charged/heavy)
        self.click_resonance()                   # resonance skill (plunge)
        # 1st Resonance Liberation -- fire when its icon (Augusta_lib1) is lit.
        if self.liberation_available():
            self.task.wait_until(lambda: not self.liberation_available(),
                                 post_action=self.send_liberation_key, time_out=2)
            self.record_liberation_use()
        # Griffin = Enhanced Resonance Skill (3 hits). Builds Majesty, which lights
        # the 2nd-liberation icon (Augusta_lib2).
        for _ in range(self.ENHANCED_SKILL_COUNT):
            self.click_resonance()               # griffin hit (x3)
        # 2nd Resonance Liberation ("majesty"): needs 2 Majesty stacks (lights the
        # lib2 icon). Build Iuno's buff to 10, then STAY and keep rotating until
        # the icon lights -- do not give up and switch out with the buff window
        # still live (see _build_and_cast_majesty).
        self._build_and_cast_majesty()           # 2nd lib once Majesty is ready
        # ha and the trailing basics are mid-sequence melee filler -- jump-cancel
        # their (long) recovery when aggressive cancel is on to cut station time.
        self._heavy_or_prowess(cancel=agg)       # ha
        if with_basics:
            # forte_check: these basics are trailing filler AFTER the 2nd lib, so
            # spending the prowess/enhanced heavy the instant it is up here is
            # pure upside -- it cannot rob the Majesty build (already spent above).
            basic_attacks(self, 3, forte_check=self.check_prowess, cancel=agg)  # ba123
            heavy(self, cancel=agg)              # ha
        self.send_echo_key()                     # echo

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
                # build to max stacks before casting -- see _build_and_cast_majesty
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
                        self.wait_down()
                        if self._build_and_cast_majesty():
                            self.send_echo_key()
                        return self.switch_next_char()
            if self.liberation_available():
                self.logger.debug('Augusta performs single liberation')
                if self.task.wait_until(lambda: not self.liberation_available(), post_action=self.send_liberation_key,
                                        time_out=2):
                    self.record_liberation_use()
                    if time_out < 14:
                        return self.switch_next_char()
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

    def switch_next_char(self, *args, **kwargs):
        # Reactive-phase outro hardening: finish a near-full ring before swapping
        # so the swap outros (transfers her buff) instead of wasting it. No-op
        # while the scripted rotation drives (it tops off before its own outros).
        from src.combat.VariableRotation import reactive_outro_topoff
        reactive_outro_topoff(self, kwargs)
        return super().switch_next_char(*args, **kwargs)

    def on_combat_end(self, chars):
        next_char = str((self.index + 1) % len(chars) + 1)
        self.logger.debug(f'Augusta on_combat_end {self.index} switch next char: {next_char}')
        self.task.send_key(next_char)

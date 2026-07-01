import re
import time

from src.char.BaseChar import BaseChar

""" 
    几个长派生帧动作的切人时间阈值,改小可以减少站场时间
    初始为 3
"""
switch_time = 3


class Augusta(BaseChar):
    # Augusta's stacking buff (green badge, bottom-CENTRE above the resource gems)
    # maxes at 10; the 2nd lib (majesty recast) should only fire at >= TARGET
    # stacks -- below that it wastes the empowered hit. Read by OCR over the badge;
    # box is 3840x2160 reference px (auto-scaled). Nudge the box if the digit isn't
    # being read (enable debug logging to see the value).
    # Box on the digit at the badge's lower-right edge. (1935..) overshot into the
    # empty space right of the badge; (1892.. full badge) caught the clock and OCR
    # read it as "L". This sits between: the clock's lower-right where the number
    # renders.
    AUGUSTA_BUFF_STACK_BOX = (1880, 1788, 1952, 1848)
    AUGUSTA_BUFF_STACK_TARGET = 9

    def do_perform(self):
        from src.combat.StrictRotation import get_strict_rotation
        if get_strict_rotation(self.task).run_current(self):
            return
        self._do_perform_default()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        from src.combat.StrictRotation import get_strict_rotation, MUST, NO
        from src.char.BaseChar import SwitchPriority
        rot = get_strict_rotation(self.task)
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
            # 1. skill
            self.click_resonance()
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

    def _heavy_or_prowess(self):
        from src.combat.StrictRotation import heavy
        if self.check_prowess():
            self.perform_prowess()
        else:
            heavy(self)

    def _build_buff_stack(self):
        """One stack-building action for the build-to-target loop.

        Plain basics barely move the buff; her forte heavy / prowess builds the
        bulk of it, so spend that when it is up and otherwise fall back to a basic.
        """
        if self.is_forte_full() and self.heavy_click_forte(self.is_forte_full):
            return
        if self.check_prowess() and self.perform_prowess():
            return
        self.click()

    def _augusta_burst(self, with_basics):
        from src.combat.StrictRotation import heavy, basic_attacks
        # The stacking buff comes from Iuno's FULL-CONCERTO outro into Augusta, so
        # the badge only exists after that switch. Capture it here (has_intro is
        # set by a full-concerto switch): only THEN do we run the buff-stack OCR /
        # 2nd lib. Captured at entry because attacks below can clear the flag.
        got_iuno_outro = self.has_intro
        self._heavy_or_prowess()                 # ha
        # Augusta's griffin liberation has a ~25s cooldown, so it is NOT up every
        # burst. Cast it only when actually off cooldown -- has_cd() is the OCR'd
        # timer; the lit-icon check false-positives during the CD and made the cast
        # fail 'no effect' every burst. griffin = did it actually summon this burst.
        griffin = False
        if not self.has_cd('liberation'):
            griffin = self.click_liberation()    # lib -> summons griffin
        else:
            self.logger.info('Augusta burst: griffin lib on cooldown (~25s), skipping griffin')
        self.click_resonance()                   # skill
        self._heavy_or_prowess()                 # ha
        # 2nd lib (majesty) is a RECAST of the griffin, so it can only fire when the
        # griffin actually summoned this burst -- and only when she entered via
        # Iuno's full-concerto outro (else there is no buff to stack). At 1..8 stacks
        # don't skip -- build up to the target first (bounded), then cast; 0 (OCR
        # miss) and >=target cast directly.
        if got_iuno_outro and griffin:
            stacks = self.buff_stacks()
            if 1 <= stacks < self.AUGUSTA_BUFF_STACK_TARGET:
                self.logger.info(
                    f'Augusta burst: {stacks} stacks, building to '
                    f'{self.AUGUSTA_BUFF_STACK_TARGET} before the 2nd lib')
                self.task.wait_until(
                    lambda: self.buff_stacks() >= self.AUGUSTA_BUFF_STACK_TARGET,
                    post_action=self._build_buff_stack, time_out=5)
            self.perform_majesty()               # 2nd lib (recast of the griffin)
        elif got_iuno_outro:
            self.logger.info('Augusta burst: no griffin this burst, skipping 2nd lib')
        if with_basics:
            basic_attacks(self, 3)               # ba123
            heavy(self)                          # ha
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
                self.logger.debug('Augusta performs majesty')
                if self.perform_majesty():
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
                        if self.perform_majesty():
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

    def buff_stacks(self):
        """OCR Augusta's stacking-buff count badge (0 if it can't be read).

        The count is a white digit on the green clock badge, so isolate white
        text first: that blacks out the clock symbol and green fill and leaves
        just the number, which reads far more reliably.
        """
        from src.task.BaseWWTask import isolate_white_text_to_black
        box = self.task.box_of_screen_scaled(
            3840, 2160, *self.AUGUSTA_BUFF_STACK_BOX,
            name='augusta_buff_stacks', hcenter=True)
        self.task.draw_boxes(box.name, box)  # visible in debug overlay for tuning
        stacks = 0
        for t in self.task.ocr(box=box, match=re.compile(r'\d+'),
                               frame_processor=isolate_white_text_to_black):
            try:
                stacks = max(stacks, int(re.sub(r'\D', '', t.name)))
            except (ValueError, TypeError):
                continue
        self.logger.debug(f'Augusta buff_stacks = {stacks}')
        return stacks

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

    def on_combat_end(self, chars):
        next_char = str((self.index + 1) % len(chars) + 1)
        self.logger.debug(f'Augusta on_combat_end {self.index} switch next char: {next_char}')
        self.task.send_key(next_char)

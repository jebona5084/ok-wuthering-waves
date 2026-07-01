import time
import cv2
import numpy as np
from ok import color_range_to_bound
from src.char.BaseChar import BaseChar, SwitchPriority


class Ciaccona(BaseChar):
    """Ciaccona tuned for the Cartethyia / Aero Rover team.

    Rotation:
      intro N1 -> early echo (summon slot) -> ground reset -> resonance ->
      forte>=3: jump + heavy hold -> liberation -> switch.

    Team logic:
      - Holds the field (SwitchPriority.NO) while her liberation field is up
        AND Cartethyia is still in Fleurdelys form, so the buff window covers
        the Fleurdelys outro.
      - Rushes her own rotation (need_fast_perform) whenever Fleurdelys is
        waiting off-field, since Cartethyia returns MUST in that state.
      - Records outrotime on full-con switch-out so Cartethyia can detect the
        30s outro buff window and extend her N4 field time.
    """

    LIB_LOCK_SHORT = 8
    LIB_LOCK_LONG = 20
    OUTRO_WINDOW = 30

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.intro_motion_freeze_duration = 0.73
        self.in_liberation = False
        self.cartethyia = None
        self.teammate_decided = False
        self.outrotime = -1

    def skip_combat_check(self):
        # liberation cast animation can briefly hide combat UI
        return self.time_elapsed_accounting_for_freeze(self.last_liberation) < 2

    def reset_state(self):
        super().reset_state()
        self.teammate_decided = False
        self.cartethyia = None

    def decide_teammate(self):
        if self.teammate_decided:
            return
        # name-based lookup: custom-loaded char classes inherit BaseChar,
        # not the built-in class, so task.has_char(Cartethyia) misses them
        self.cartethyia = next(
            (c for c in self.task.chars
             if c is not None and type(c).__name__ == 'Cartethyia'), None)
        self.teammate_decided = True
        self.logger.debug(f'ciaccona teammate cartethyia: {self.cartethyia}')

    def fleurdelys_waiting(self):
        """Cartethyia transformed and waiting to come back on-field."""
        return self.cartethyia is not None and not self.cartethyia.is_cartethyia

    def lib_field_active(self):
        """Her liberation wind field is currently up.

        Time-based on last_liberation ON PURPOSE: in_liberation is a
        per-turn cast flag reset at the top of every do_perform, so it went
        False the moment she took another slot mid-field — teammates then
        read the field as down while it had ~16s left, which broke
        Cartethyia's transform prioritization.
        """
        return (self.last_liberation > 0
                and self.time_elapsed_accounting_for_freeze(
                    self.last_liberation) < self.LIB_LOCK_LONG)

    def cartethyia_holding_nuke(self):
        """Cartethyia has buffs banked and is deferring her transform
        until this character's liberation field goes down."""
        return (self.cartethyia is not None
                and getattr(self.cartethyia, 'nuke_ready', False)
                and not self.lib_field_active())

    def dodge_cancel(self):
        """Tap-dodge to cancel skill backswing.

        Only meaningful when staying on field for a follow-up action —
        switching out cancels backswing for free. Skipped while airborne,
        where a dodge becomes an aerial dash and disrupts positioning.
        """
        if self.flying():
            return False
        self.continues_right_click(0.05)
        self.sleep(0.05)
        return True

    def need_fast_perform(self):
        if self.fleurdelys_waiting():
            self.logger.debug('fast perform: Fleurdelys waiting off-field')
            return True
        return super().need_fast_perform()

    def do_perform(self):
        self.decide_teammate()
        self.in_liberation = False
        wait = False
        jump = True

        fast = self.need_fast_perform()
        if self.has_intro:
            self.continues_normal_attack(0.8)
            if not fast:
                self.continues_normal_attack(0.7)

        # Cartethyia is holding her transform for the wind field: the Q is
        # the only thing that matters this slot — cast it first and hand
        # the field straight back for the nuke
        if self.cartethyia_holding_nuke() and self.liberation_available():
            self.logger.info('lib-first: Cartethyia holding nuke for the field')
            self.click_echo(time_out=0)
            if self.click_liberation():
                self.in_liberation = True
                # the next slot is the transform burst — full concerto here
                # lands her intro AND aero-amp outro directly on the nuke.
                # Widest top-off window in the kit: <=0.8s against an 18s
                # field is nothing, the outro on Fleurdelys is everything
                con = self.get_current_con()
                if 0.6 <= con < 1:
                    self.logger.debug(
                        f'lib-first: topping off concerto from {con:.2f} for the outro')
                    self.continues_normal_attack(0.8, until_con_full=True)
                return self.switch_next_char()

        # use echo early if the summon-type echo slot is loaded
        if self.current_echo() < 0.22:
            self.click_echo(time_out=0)

        # ground reset so resonance comes out clean — only worth the ~1.8s
        # when the E is actually castable (skip when rushing or E on cd)
        if (not self.has_intro and not fast and not self.is_mouse_forte_full()
                and self.resonance_available()):
            self.click_jump_with_click(0.4)
            self.task.wait_until(lambda: not self.flying(),
                                 post_action=self.click_with_interval, time_out=1.2)
            self.continues_normal_attack(0.2)

        if self.click_resonance()[0]:
            jump = False
            wait = True
            # no dodge here: her E grants its effect mid-animation and there
            # is no on-screen icon to confirm it landed, so canceling the
            # backswing risks clipping the cast itself

        # forte >= 3: airborne heavy to dump the gauge
        if self.judge_forte() >= 3:
            if jump:
                start = time.time()
                while not self.flying():
                    self.task.jump(after_sleep=0.01)
                    if time.time() - start > 0.3:
                        break
                    self.task.next_frame()
            self.heavy_click_forte(check_fun=self.is_mouse_forte_full)
            # plunge effect lands on impact; only the landing recovery
            # remains, which is safe to cancel before liberation
            self.dodge_cancel()
            wait = True

        if self.liberation_available():
            if wait:
                # full settle: covers the uncanceled E backswing so the
                # liberation input registers after the cast completes
                self.sleep(0.4)
            if self.click_liberation():
                self.in_liberation = True

        if not self.in_liberation and self.current_echo() > 0.25:
            self.click_echo()
        # concerto powers the whole interlock: full con grants the next char
        # an intro AND opens the 30s outro window that gives Cartethyia her
        # full 3.25s N4 duration — top off when close instead of leaving
        # 80-95% con on the table (bounded, exits the moment it fills)
        if not fast:
            con = self.get_current_con()
            if 0.7 <= con < 1:
                self.logger.debug(f'topping off concerto from {con:.2f}')
                self.continues_normal_attack(0.8, until_con_full=True)
        self.switch_next_char()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        self.decide_teammate()
        # time-based like lib_field_active: the per-turn in_liberation flag
        # is unreliable as a lock condition once she has taken another slot
        if self.last_liberation > 0:
            elapsed = self.time_elapsed_accounting_for_freeze(self.last_liberation)
            # keep the wind field running through Cartethyia's Fleurdelys window
            if elapsed < self.LIB_LOCK_SHORT or (
                    elapsed < self.LIB_LOCK_LONG and self.fleurdelys_waiting()):
                return SwitchPriority.NO
        if self.cartethyia_holding_nuke() and self.liberation_available():
            # Cartethyia deferred her transform waiting on this Q —
            # claim the next slot to put the field down immediately
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def switch_next_char(self, *args, **kwargs):
        if self.is_con_full():
            self.outrotime = time.time()
        return super().switch_next_char(*args, **kwargs)

    def in_outro(self):
        """Outro buff window used by Cartethyia to extend field time."""
        return self.time_elapsed_accounting_for_freeze(self.outrotime) < self.OUTRO_WINDOW

    def click_jump_with_click(self, delay=0.1):
        start = time.time()
        click = 1
        while True:
            if time.time() - start > delay:
                break
            if click == 0:
                self.task.jump(after_sleep=0.01)
            else:
                self.click()
            click = 1 - click
            self.check_combat()
            self.task.next_frame()

    def judge_forte(self):
        if self.is_mouse_forte_full():
            return 3
        box = self.task.box_of_screen_scaled(3840, 2160, 1612, 1987, 2188, 2008,
                                             name='ciaccona_forte', hcenter=True)
        return self.calculate_forte_num(ciaccona_forte_color, box, 3, 12, 14, 100)

    def judge_frequncy_and_amplitude(self, gray, min_freq, max_freq, min_amp):
        height, width = gray.shape[:]
        if height == 0 or width < 64 or not np.array_equal(np.unique(gray), [0, 255]):
            return 0
        profile = np.sum(gray == 255, axis=0).astype(np.float32)
        profile -= np.mean(profile)
        n = np.abs(np.fft.fft(profile))
        amplitude = 0
        frequncy = 0
        i = 1
        while i < width:
            if n[i] > amplitude:
                amplitude = n[i]
                frequncy = i
            i += 1
        self.logger.debug(f'forte with freq {frequncy} & amp {amplitude}')
        return (min_freq <= frequncy <= max_freq) or amplitude >= min_amp

    def calculate_forte_num(self, forte_color, box, num=1, min_freq=39, max_freq=41, min_amp=50):
        cropped = box.crop_frame(self.task.frame)
        lower_bound, upper_bound = color_range_to_bound(forte_color)
        image = cv2.inRange(cropped, lower_bound, upper_bound)

        height, width = image.shape
        step = int(width / num)

        forte = num
        left = step * (forte - 1)
        while forte > 0:
            gray = image[:, left:left + step]
            if self.judge_frequncy_and_amplitude(gray, min_freq, max_freq, min_amp):
                break
            left -= step
            forte -= 1
        self.logger.info(f'Frequncy analysis with forte {forte}')
        return forte


ciaccona_forte_color = {
    'r': (70, 100),  # Red range
    'g': (240, 255),  # Green range
    'b': (180, 210)  # Blue range
}
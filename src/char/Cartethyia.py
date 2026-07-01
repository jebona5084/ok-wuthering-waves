import time
import cv2
import numpy as np
from src.char.BaseChar import BaseChar, SwitchPriority, forte_white_color


class Cartethyia(BaseChar):
    """Cartethyia tuned for the Ciaccona / Aero Rover team.

    Rotation (small form):
      acquire missing sword buffs -> mid-air attack -> liberation (transform) ->
      resonance (watching for big-lib) -> N4 field time -> big-lib if up -> switch.

    Rotation (Fleurdelys, returning via MUST priority):
      resonance with big-lib watch -> N4 for fleurdelys_n4_duration -> big-lib ->
      back to small form -> switch.

    Team logic:
      - SwitchPriority.MUST while in Fleurdelys form, so the team funnels field
        time back to her quickly between Ciaccona / Rover funnels.
      - Extends N4 field duration to the full 3.25s while inside Ciaccona's
        30s outro buff window (ciaccona.in_outro()).
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_cartethyia = True
        self.buffs = {'sword1': None, 'sword2': None, 'sword3': None}
        self.template_shape = None
        self.try_mid_air_attack_once = False
        self.transform = False
        self.res_time = -1
        self.n4_time = -1
        self.ciaccona = None
        self.rover = None
        self.teammate_decided = False
        self.lib_ready_since = -1
        self._buffs_confirmed_at = -1
        self.nuke_ready = False
        self.transform_defer_since = -1
        self.init_template()

    # transform is hard-gated on all 3 sword buffs; if liberation sits ready
    # this long without completing them, transform anyway to avoid a deadlock
    BUFF_WAIT_TIMEOUT = 12
    # hold the transform up to this long waiting for Ciaccona's liberation
    # field; assumed field uptime once she casts
    TRANSFORM_DEFER_TIMEOUT = 8
    CIACCONA_FIELD_DURATION = 18
    # Ciaccona's liberation cooldown: within this of her last cast, her Q
    # cannot be ready regardless of what the stale off-field cd cache says
    CIACCONA_LIB_CD = 20
    # detection: full icon at high precision, OR top-half shape match that is
    # additionally verified as LIT — normalized template matching also matches
    # the dim/inactive icon (same shape, lower brightness), so a shape match
    # alone produces false positives and instant transforms
    BUFF_FULL_THRESHOLD = 0.9
    BUFF_HALF_THRESHOLD = 0.85
    BUFF_LIT_RATIO = 0.85  # matched region mean brightness vs template mean
    BUFF_SAMPLES = 3

    @property
    def intro_motion_freeze_duration(self):
        return 0.6 if self.is_cartethyia else 0.78

    @intro_motion_freeze_duration.setter
    def intro_motion_freeze_duration(self, _):
        pass

    def reset_state(self):
        super().reset_state()
        self.teammate_decided = False
        self.ciaccona = None
        self.rover = None
        # reset the deadlock-fallback anchor: a timestamp left over from a
        # previous combat would make the BUFF_WAIT_TIMEOUT fire immediately
        # and cause an instant transform at the start of the next fight
        self.lib_ready_since = -1
        self._buffs_confirmed_at = -1
        # stale nuke state from a previous fight would wrongly claim MUST
        # slots / defer the first transform of the next one
        self.nuke_ready = False
        self.transform_defer_since = -1

    def decide_teammate(self):
        if self.teammate_decided:
            return
        # name-based lookup: custom-loaded char classes inherit BaseChar,
        # not the built-in class, so task.has_char(Ciaccona) misses them
        self.ciaccona = next(
            (c for c in self.task.chars
             if c is not None and type(c).__name__ == 'Ciaccona'), None)
        self.rover = next(
            (c for c in self.task.chars
             if c is not None and type(c).__name__ == 'HavocRover'), None)
        self.teammate_decided = True
        self.logger.debug(f'cartethyia teammate ciaccona: {self.ciaccona}')

    ROVER_OPENER_DURATION = 10

    def rover_opener_active(self):
        """Rover owns the first seconds of the fight — mirror of Rover's
        own in_opener() so both sides agree on the window."""
        if self.rover is None:
            return False
        start = getattr(self.task, 'combat_start', -1)
        return (start > 0 and self.time_elapsed_accounting_for_freeze(start)
                < self.ROVER_OPENER_DURATION)

    def in_ciaccona_outro(self):
        return (self.ciaccona is not None
                and hasattr(self.ciaccona, 'in_outro')
                and self.ciaccona.in_outro())

    def buffs_complete(self):
        """Gate for the transform liberation: all 3 sword buffs must be up.

        A just-confirmed result is cached for 1s so the back-to-back checks
        in acquire_missing_buffs and do_perform don't pay the debounced
        read twice on the critical path before the transform.

        Falls back to allowing the transform if liberation has been sitting
        ready for BUFF_WAIT_TIMEOUT seconds, so a single undetectable buff
        cannot deadlock the rotation.
        """
        if self._buffs_confirmed_at > 0 and self.time_elapsed_accounting_for_freeze(
                self._buffs_confirmed_at) < 1.0:
            return True
        self.get_sword_buffs(samples=self.BUFF_SAMPLES)
        if all(self.buffs.values()):
            self.lib_ready_since = -1
            self._buffs_confirmed_at = time.time()
            return True
        if self.liberation_available():
            if self.lib_ready_since < 0:
                self.lib_ready_since = time.time()
            elif self.time_elapsed_accounting_for_freeze(
                    self.lib_ready_since) > self.BUFF_WAIT_TIMEOUT:
                self.logger.info(
                    f'buff wait timed out ({self.buffs}), transforming anyway')
                return True
        return False

    def init_template(self):
        """Pre-build top-half templates for all three sword buffs.

        The bottom half of the buff icons is obscured by the pulsing glow
        animation, so full-template matching at high thresholds produces
        false negatives. The top half stays stable (same trick the built-in
        code uses for is_small and sword2 acquisition).

        Also records each template's own brightness: the saved assets show
        the LIT buff, while the inactive slot displays the same icon dimmed.
        Shape matching alone cannot tell them apart, so detection requires
        the on-screen crop to be nearly as bright as the template.
        """
        self.template_shape = self.task.frame.shape[:2]
        self.sword_templates = {}
        for name in ('forte_cartethyia_sword1', 'forte_cartethyia_sword2',
                     'forte_cartethyia_sword3'):
            template = self.task.get_feature_by_name(name)
            full_mat = template.mat
            h = full_mat.shape[0]
            half_mat = full_mat[:int(h * 0.5)]
            half_box = self.task.get_box_by_name(name)
            half_box.height = int(h * 0.6)
            self.sword_templates[name] = {
                'half_mat': half_mat,
                'half_box': half_box,
                'half_mean': float(np.mean(cv2.cvtColor(half_mat, cv2.COLOR_BGR2GRAY))),
                'full_mean': float(np.mean(cv2.cvtColor(full_mat, cv2.COLOR_BGR2GRAY))),
            }
        self.sword3_half_mat = self.sword_templates['forte_cartethyia_sword3']['half_mat']
        self.sword3_half_box = self.sword_templates['forte_cartethyia_sword3']['half_box']

    def is_lit(self, match, template_mean, name=''):
        """True if the matched region is bright enough to be the active buff."""
        if not match:
            return False
        cropped = match.crop_frame(self.task.frame)
        if cropped is None or cropped.size == 0:
            return False
        mean_val = float(np.mean(cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)))
        lit = mean_val >= template_mean * self.BUFF_LIT_RATIO
        self.logger.debug(
            f'{name} brightness {mean_val:.0f} / template {template_mean:.0f} lit {lit}')
        return lit

    def find_sword_half(self, name):
        """Top-half shape match + brightness gate. Returns the match or None."""
        info = self.sword_templates[name]
        match = self.task.find_one(template=info['half_mat'], box=info['half_box'],
                                   threshold=self.BUFF_HALF_THRESHOLD)
        if match and self.is_lit(match, info['half_mean'], name):
            return match
        return None

    def dodge_cancel(self):
        """Tap-dodge to cancel skill backswing.

        Used only where she stays on field for a follow-up action (buff
        acquisition chains, plunge-to-transform). Switching out cancels for
        free, so on-switch paths never dodge. Skipped while airborne.
        """
        if self.flying():
            return False
        self.continues_right_click(0.05)
        self.sleep(0.05)
        return True

    def on_combat_end(self, chars):
        if not self.is_cartethyia:
            next_char = str((self.index + 1) % len(chars) + 1)
            self.logger.debug(f'on_combat_end {self.index} switch next char: {next_char}')
            start = time.time()
            while time.time() - start < 6:
                self.task.load_chars()
                current_char = self.task.get_current_char(raise_exception=False)
                if not isinstance(current_char, type(self)):
                    break
                else:
                    self.task.send_key(next_char)
                self.sleep(0.2, False)
            self.logger.debug(f'on_combat_end {self.index} switch end')

    def ciaccona_lib_active(self):
        """Ciaccona's wind field is currently up.

        Anchored on her last_liberation timestamp (set on every successful
        cast, never reset per-turn) — her in_liberation flag is wiped at the
        top of each of her visits, so reading it here showed the field as
        down while it was still running.
        """
        c = self.ciaccona
        return (c is not None and getattr(c, 'last_liberation', -1) > 0
                and c.time_elapsed_accounting_for_freeze(c.last_liberation)
                < self.CIACCONA_FIELD_DURATION)

    def ciaccona_lib_castable(self):
        """Her Q can realistically be cast right now.

        The off-field cd cache only refreshes while she is current, so it
        goes stale after she casts and leaves — guard with cast recency:
        within CIACCONA_LIB_CD of her last cast the Q cannot be ready no
        matter what the cache claims.
        """
        c = self.ciaccona
        if c is None:
            return False
        if getattr(c, 'last_liberation', -1) > 0 and \
                c.time_elapsed_accounting_for_freeze(
                    c.last_liberation) < self.CIACCONA_LIB_CD:
            return False
        return c.liberation_available()

    def should_defer_transform(self):
        """Hold the transform so Ciaccona can put her field down first.

        Defers only when her liberation is actually castable (waiting gains
        nothing while it is on cooldown) and never longer than
        TRANSFORM_DEFER_TIMEOUT, so a stale cd read cannot stall the nuke.
        """
        if self.ciaccona is None:
            return False
        if self.ciaccona_lib_active():
            self.transform_defer_since = -1
            return False
        if not self.ciaccona_lib_castable():
            self.transform_defer_since = -1
            return False
        if self.transform_defer_since < 0:
            self.transform_defer_since = time.time()
            return True
        if self.time_elapsed_accounting_for_freeze(
                self.transform_defer_since) > self.TRANSFORM_DEFER_TIMEOUT:
            self.logger.info('defer timed out, transforming without Ciaccona field')
            return False
        return True

    def transform_ready_now(self):
        """Buffs banked and nothing left to wait for — the read-only mirror
        of should_defer_transform, safe to call from get_switch_priority
        (no timer mutation, no timeout logging).

        True when the transform should happen on the very next field slot:
        the wind field is already up, there is no Ciaccona / her Q is not
        castable (no field coming), or the defer window has expired.
        """
        if not self.nuke_ready:
            return False
        if self.ciaccona_lib_active():
            return True
        if not self.ciaccona_lib_castable():
            return True
        # her field is pending: only jump the queue once the defer expires
        ready = (self.transform_defer_since > 0
                 and self.time_elapsed_accounting_for_freeze(
                     self.transform_defer_since) > self.TRANSFORM_DEFER_TIMEOUT)
        if not ready:
            self.logger.debug('transform held: waiting on Ciaccona Q for the field')
        return ready

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        if not self.is_cartethyia:
            # Fleurdelys must come back on-field as soon as possible
            return SwitchPriority.MUST
        self.decide_teammate()
        if self.transform_ready_now() and not self.rover_opener_active():
            # small form with the transform fully unblocked: claim the next
            # slot instead of waiting out the normal cycle — covers the
            # field-just-went-up case, the no-field-coming case (Ciaccona Q
            # on cooldown), and an expired defer. Yields during the opener:
            # Rover owns the first seconds of the fight.
            self.logger.debug('transform ready: claiming the next slot')
            return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def quick_skill_then_switch(self, fallback_attack=0.3):
        """Yield-slot action: fire the E, give it just enough commit time,
        then hand the field over — the switch-out cancels the backswing for
        free. When sword3 isn't banked yet, wait for the lit icon before
        leaving (the E grants it mid-animation; switching on input
        registration clips the cast and loses the buff)."""
        if self.resonance_available() and self.click_resonance()[0]:
            self.check_combat()
            if not self.find_sword_buff('sword3'):
                self.task.wait_until(
                    lambda: self.find_sword_buff('sword3'), time_out=1.2)
            else:
                # buff already banked: just let the cast come out
                self.sleep(0.35)
            return self.switch_next_char()
        if fallback_attack > 0:
            self.continues_normal_attack(fallback_attack)
        return self.switch_next_char()

    def do_perform(self):
        self.decide_teammate()
        self.transform = False
        if self.has_intro:
            self.continues_normal_attack(1.2)
        else:
            self.click_echo(time_out=0)
        if self.is_small():
            self.logger.info('is cartethyia')
            self.wait_down()
            if self.rover_opener_active() and not self.nuke_ready:
                # opener: hand the field back to Rover fast — echo was
                # already dropped above; fire the E on the way out. It does
                # real damage, builds concerto, AND banks sword3 early,
                # shortening the post-opener acquisition by its whole step
                self.logger.info('opener: short slot, skill and out to Rover')
                return self.quick_skill_then_switch(fallback_attack=0.3)
            if self.acquire_missing_buffs():
                return self.switch_next_char()
            ready = self.buffs_complete()
            if ready:
                # signal Ciaccona (lib-first fast path) and Rover (yields the
                # post-Ciaccona slot) that the transform burst is queued
                self.nuke_ready = True
                if self.should_defer_transform():
                    self.logger.info(
                        'nuke ready — holding transform for Ciaccona liberation')
                    # buffs are banked, so the E here is pure damage plus
                    # concerto toward the transform intro on the way out
                    return self.quick_skill_then_switch(fallback_attack=0.2)
            self.check_combat()
            self.try_mid_air_attack()
            self.check_combat()
            if ready and self.click_liberation():
                self.is_cartethyia = False
                self.last_res = -1
                self.transform = True
                self.lib_ready_since = -1
                self._buffs_confirmed_at = -1
                self.nuke_ready = False
                self.transform_defer_since = -1
            elif not self.is_small():
                self.transform = True
                self.nuke_ready = False
        else:
            self.logger.info('is fleurdelys')
        if not self.is_cartethyia:
            # fire echo inside the Fleurdelys damage window (instant key,
            # no-op if on cooldown or already used this rotation)
            self.click_echo(time_out=0)
        if self.click_resonance_with_lib_big():
            pass
        else:
            time_out = 1.1 if self.is_small() else self.fleurdelys_n4_duration()
            start = time.time()
            while time.time() - start < time_out:
                if self.try_lib_big():
                    return self.switch_next_char()
                self.click_with_interval()
                self.check_combat()
                self.task.next_frame()
            self.n4_time = time.time()
        self.try_lib_big()
        self.switch_next_char()

    def fleurdelys_n4_duration(self):
        if not self.transform and self.has_intro:
            duration = 3.9 - (time.time() - self.last_perform)
        elif self.transform or self.is_first_engage() or self.in_ciaccona_outro() or \
                self.time_elapsed_accounting_for_freeze(self.n4_time, intro_motion_freeze=True) < 1.5:
            # full field window: fresh transform, combat start, or inside
            # Ciaccona's outro buff window
            duration = 3.25
        elif (backswing := self.time_elapsed_accounting_for_freeze(
                self.res_time, intro_motion_freeze=True)) < 2.5:
            duration = 2 + max(0, 1.6 - backswing)
        else:
            duration = 1.9 - (time.time() - self.last_perform)
        self.n4_time = -1
        self.res_time = -1
        self.logger.debug(f'fleurdelys_n4_duration {duration}')
        return duration

    def click_resonance_with_lib_big(self):
        if self.has_cd('resonance'):
            return False
        clicked = False
        self.logger.debug('click_resonance start')
        last_click = 0
        resonance_click_time = 0
        while True:
            if resonance_click_time != 0 and time.time() - resonance_click_time > 8:
                self.task.in_liberation = False
                self.logger.error(
                    f'click_resonance too long, breaking {time.time() - resonance_click_time}')
                self.task.screenshot('click_resonance too long, breaking')
                break
            self.check_combat()
            now = time.time()
            current_resonance = self.current_resonance()
            if not self.resonance_available():
                self.logger.debug('click_resonance not available break')
                break
            self.logger.debug(f'click_resonance resonance_available click {current_resonance}')

            if now - last_click > 0.1:
                if current_resonance > 0 and self.resonance_available():
                    if current_resonance < 0.17 and time.time() - resonance_click_time < 2.5:
                        self.click()
                        continue
                    if resonance_click_time == 0:
                        clicked = True
                        resonance_click_time = now
                    self.send_resonance_key()
                last_click = now
            if self.try_lib_big():
                break
            self.task.next_frame()
        if clicked:
            self.record_resonance_use()
            self.res_time = time.time()
        return clicked

    def is_mid_air_attack_available(self):
        if self.is_cartethyia:
            box = self.task.box_of_screen_scaled(3840, 2160, 2298, 1997, 2361, 2022,
                                                 name='inner_cartethyia_space', hcenter=True)
            self.task.draw_boxes(box.name, box)
            if self.task.calculate_color_percentage(forte_white_color, box) > 0.15:
                cropped = box.crop_frame(self.task.frame)
                gray = cv2.cvtColor(cropped, cv2.COLOR_BGR2GRAY)
                mean_val = np.mean(gray)
                contrast_val = np.std(gray)
                self.logger.debug(f'cartethyia_space mean {mean_val} contrast {contrast_val}')
                return mean_val > 190 and contrast_val < 45

    def try_mid_air_attack(self, timeout=2):
        self.get_sword_buffs()
        if self.liberation_available() or all(self.buffs.values()) or self.try_mid_air_attack_once:
            pass
        else:
            return
        if self.is_mid_air_attack_available():
            self.logger.info('perform mid-air attack')
            start = time.time()
            while True:
                self.task.jump(after_sleep=0.1)
                self.task.click(after_sleep=0.1)
                if not self.is_mid_air_attack_available():
                    # let the plunge land, then dodge-cancel the landing
                    # recovery so the transform liberation comes out sooner
                    self.sleep(0.2)
                    if not self.dodge_cancel():
                        self.sleep(0.2)
                    break
                if time.time() - start > timeout:
                    break
                self.sleep(0.1)
        elif self.try_mid_air_attack_once:
            start = time.time()
            while time.time() - start < 0.8:
                self.task.jump(after_sleep=0.1)
                self.task.click(after_sleep=0.1)
        self.try_mid_air_attack_once = False

    def is_small(self):
        if self.template_shape != self.task.frame.shape[:2]:
            self.init_template()
        self.is_cartethyia = bool(self.task.find_one(template=self.sword3_half_mat,
                                                     box=self.sword3_half_box, threshold=0.5))
        return self.is_cartethyia

    def try_lib_big(self):
        if self.is_lib_big_available():
            if self.click_liberation():
                self.is_cartethyia = True
                self.click_resonance()
                return True

    def is_lib_big_available(self):
        if big := self.task.find_one('lib_cartethyia_big'):
            self.logger.debug('lib cartethyia big available {}'.format(big.confidence))
            self._liberation_available = True
            return True

    def find_sword_buff(self, key):
        """Single-frame check: full or top-half shape match, both lit-verified.

        Normalized template matching is contrast-invariant, so the dim
        inactive icon matches the lit template on shape alone — every match
        must pass the is_lit brightness gate.
        """
        name = f'forte_cartethyia_{key}'
        info = self.sword_templates[name]
        full = self.task.find_one(name, threshold=self.BUFF_FULL_THRESHOLD)
        if full and self.is_lit(full, info['full_mean'], name):
            return True
        return bool(self.find_sword_half(name))

    def get_sword_buffs(self, samples=1):
        """Read sword buffs, optionally debounced across multiple frames.

        A buff counts as present if it is seen in ANY sampled frame —
        the icons pulse, so absence must be consistent to be believed.
        """
        if self.template_shape != self.task.frame.shape[:2]:
            self.init_template()
        result = {'sword1': False, 'sword2': False, 'sword3': False}
        for i in range(max(1, samples)):
            for key in result:
                if not result[key]:
                    result[key] = self.find_sword_buff(key)
            if all(result.values()):
                break
            if i < samples - 1:
                self.task.next_frame()
        self.buffs = result
        self.logger.debug(f'buffs {self.buffs}')
        return self.buffs

    def _acquire_sword2(self, max_time_out=3.5):
        """Sword2 comes from the basic-attack string: ground N1 clicks."""
        sword2 = 'forte_cartethyia_sword2'
        time_out = max_time_out
        if try_once := bool(self.find_sword_half(sword2)):
            time_out = 2 if not self.is_first_engage() else 2.5
        start = time.time()
        interrupt_handled = False
        while time.time() - start < time_out:
            if not try_once and self.find_sword_half(sword2):
                break
            if not interrupt_handled and self.flying():
                time_out = 2.5 if time_out == 2 else time_out
                interrupt_handled = True
                self.task.wait_until(lambda: not self.flying(), time_out=3)
                start = time.time()
            self.click(interval=0.1, after_sleep=0.01)
            self.check_combat()
            self.task.next_frame()
        self.logger.debug(f'sword2: click duration {time.time() - start}')

    def _acquire_sword3(self):
        """Sword3 comes from resonance (E)."""
        if not self.click_resonance()[0]:
            return
        self.check_combat()
        # the E grants sword3 MID-ANIMATION — click_resonance returns on
        # input registration, not animation end, so dodging immediately
        # clips the cast and the buff is lost. Wait until the lit icon
        # confirms the buff landed, then cancel only the tail recovery.
        if self.task.wait_until(lambda: self.find_sword_buff('sword3'),
                                time_out=1.5):
            self.dodge_cancel()
        else:
            self.logger.debug('sword3 not confirmed after E, no cancel')
            self.sleep(0.2)

    def _acquire_sword1(self):
        """Sword1 comes from the heavy attack hold."""
        self.task.mouse_down()
        start = time.time()
        while time.time() - start < 1.5:
            if self.find_sword_buff('sword1'):
                break
            self.task.next_frame()
        self.task.mouse_up()
        self.check_combat()
        self.logger.debug(f'sword1: heavy_att duration {time.time() - start}')
        # only cancel the release recovery once the buff is confirmed —
        # if the hold timed out without it, dodging could clip a heavy
        # that is still completing
        if self.task.wait_until(lambda: self.find_sword_buff('sword1'),
                                time_out=0.8):
            self.dodge_cancel()

    def acquire_missing_buffs(self):
        """Strict buff pipeline: sword2 -> sword1 -> sword3.

        Sword2 is built by the basic-attack string, and casting E (sword3)
        or heavy (sword1) RESETS that string — so neither is allowed until
        sword2 is confirmed lit on screen. If sword2 cannot be confirmed,
        she switches out and retries it next field window (her intro N1s
        also feed the string); the 12s liberation fallback in
        buffs_complete() remains the deadlock escape.

        After sword2, the heavy (sword1) goes before the E (sword3) so the
        rotation ends on the E — leaving its effect freshest going into the
        transform and the E cooldown rolling at the latest possible moment.
        """
        self.get_sword_buffs(samples=2)
        if all(self.buffs.values()):
            return False
        self.logger.info(f'acquire missing buffs {self.buffs}')
        if not any(self.buffs.values()):
            self.try_mid_air_attack_once = True

        # --- step 1: sword2, hard-gated before anything else ---
        if not self.buffs.get('sword2'):
            # one shorter in-place retry: a hit can interrupt the N1 string,
            # and retrying here costs ~2s vs a full team cycle to come back
            for attempt, time_out in enumerate((3.5, 2.0)):
                self._acquire_sword2(time_out)
                if self.task.wait_until(lambda: self.find_sword_buff('sword2'),
                                        time_out=0.6):
                    self.buffs['sword2'] = True
                    break
                self.logger.info(f'sword2 not confirmed (attempt {attempt + 1})')
            else:
                self.logger.info(
                    'sword2 not confirmed — holding E/heavy, retry next window')
                return not self.buffs_complete()

        # --- step 2: sword1 via heavy hold ---
        if not self.buffs.get('sword1'):
            self._acquire_sword1()

        # --- step 3: sword3 via resonance ---
        if not self.buffs.get('sword3'):
            self._acquire_sword3()

        # final debounced re-check (inside buffs_complete); switch out while
        # buffs are still incomplete so teammates keep the rotation moving —
        # do_perform only transforms once buffs_complete() passes
        return not self.buffs_complete()
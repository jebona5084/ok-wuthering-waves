import time
from src.char.BaseChar import BaseChar, Elements, SwitchPriority


class HavocRover(BaseChar):
    """Rover tuned for the Ciaccona / Cartethyia (Aero) team.

    Wind rotation:
      intro aerial clicks -> echo -> resonance into the aerial loop ->
      aerial clicks ~1.74s -> liberation -> wait down -> forte resonance -> switch.

    Team logic:
      - ALWAYS takes the field after Ciaccona (SwitchPriority.MUST when the
        outgoing char is Ciaccona), so the cycle order is fixed:
        Cartethyia -> Ciaccona -> Rover -> Cartethyia. This rule yields to
        a Fleurdelys-waiting Cartethyia, whose own MUST priority and ticking
        transform timer outrank the ordering preference.
      - Runs the abbreviated fast_perform_wind_routine whenever a teammate
        returns SwitchPriority.MUST (Fleurdelys waiting off-field), via the
        BaseChar need_fast_perform() check.
      - use_skyfall_severance stays off without Phoebe, so the standard single
        aerial window is used.

    Other elements keep the built-in routines so this file is safe for any
    Rover variant.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_skyfall_severance = False
        self.char_cartethyia = None
        self.char_ciaccona = None
        self.teammates_decided = False
        self.lib_hold_since = -1
        self.field_hold_since = -1

    def reset_state(self):
        self.ring_index = -1
        self.char_cartethyia = None
        self.char_ciaccona = None
        self.teammates_decided = False
        # stale hold anchors from a previous combat would fire the timeout
        # fallbacks immediately in the next fight
        self.lib_hold_since = -1
        self.field_hold_since = -1
        super().reset_state()

    def decide_teammates(self):
        if self.teammates_decided:
            return
        # name-based lookup: custom-loaded char classes inherit BaseChar,
        # not the built-in class, so task.has_char(Cartethyia) misses them
        self.char_cartethyia = next(
            (c for c in self.task.chars
             if c is not None and type(c).__name__ == 'Cartethyia'), None)
        self.char_ciaccona = next(
            (c for c in self.task.chars
             if c is not None and type(c).__name__ == 'Ciaccona'), None)
        self.teammates_decided = True

    def fleurdelys_waiting(self):
        self.decide_teammates()
        return (self.char_cartethyia is not None
                and not self.char_cartethyia.is_cartethyia)

    def cartethyia_nuke_pending(self):
        self.decide_teammates()
        return (self.char_cartethyia is not None
                and getattr(self.char_cartethyia, 'nuke_ready', False))

    # opener: funnel field time to Rover for the first N seconds of combat
    OPENER_DURATION = 10

    def in_opener(self):
        start = getattr(self.task, 'combat_start', -1)
        return (start > 0 and self.time_elapsed_accounting_for_freeze(start)
                < self.OPENER_DURATION)

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        # opener: Rover owns the first OPENER_DURATION seconds — claim every
        # switch so he holds the field while teammates run short slots.
        # Yields only to a waiting Fleurdelys, whose timer outranks anything.
        if self.in_opener() and not self.fleurdelys_waiting():
            return SwitchPriority.MUST
        # fixed ordering: Rover always follows Ciaccona — unless Fleurdelys
        # is waiting OR Cartethyia is holding a buffed transform; both of
        # those MUSTs must win the post-Ciaccona slot unopposed (the nuke
        # has to land right after the field goes down, not after Rover)
        if current_char is not None and type(current_char).__name__ == 'Ciaccona':
            if not self.fleurdelys_waiting() and not self.cartethyia_nuke_pending():
                return SwitchPriority.MUST
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def do_perform(self):
        self.init()
        if not self.has_intro:
            self.sleep(0.01)
        if self.ring_index == Elements.WIND:
            self.intro_motion_freeze_duration = 0.52
            if self.need_fast_perform():
                self.logger.debug('wind fast perform: teammate MUST priority')
                self.fast_perform_wind_routine()
            else:
                self.perform_wind_routine()
        elif self.ring_index == Elements.HAVOC:
            self.intro_motion_freeze_duration = 0.64
            self.perform_havoc_routine()
        elif self.ring_index == Elements.SPECTRO:
            self.intro_motion_freeze_duration = 0.92
            self.perform_spectro_routine()
        else:
            self.perform_basic_routine()
        self.switch_next_char()

    def init(self):
        if self.ring_index == -1:
            self.task._ensure_ring_index()
            if self.ring_index == Elements.WIND:
                self.init_wind()

    def init_wind(self):
        self.decide_teammates()
        self.use_skyfall_severance = False
        has_phoebe = any(c is not None and type(c).__name__ == 'Phoebe'
                         for c in self.task.chars)
        if self.char_cartethyia is not None and has_phoebe:
            self.use_skyfall_severance = True
        self.logger.debug(f'rover wind skyfall_severance {self.use_skyfall_severance}')

    # ------------------------------------------------------------------ wind

    # liberation is held until the forte bar reads at least this fill
    # cast liberation whenever it is available: no forte minimum, no
    # field-preference hold. Set False to restore the gated behavior below.
    LIB_WHEN_AVAILABLE = True
    LIB_MIN_FORTE = 0.5
    # if liberation has sat ready this long with forte still below the
    # threshold, cast anyway — a miscalibrated forte color range must not
    # permanently block the liberation
    LIB_HOLD_TIMEOUT = 20

    def forte_percent(self):
        """Approximate forte bar fill 0.0-1.0.

        Thin horizontal strip through the standard forte bar (same technique
        as Zani's blazes meter): the fraction of strip pixels matching the
        bar color equals the fill fraction. Calibrate rover_wind_forte_color
        at the bottom of this file against the debug log if readings look off.
        """
        box = self.task.box_of_screen_scaled(3840, 2160, 1630, 2002, 2176, 2005,
                                             name='rover_forte', hcenter=True)
        self.task.draw_boxes(box.name, box)
        percent = self.task.calculate_color_percentage(rover_wind_forte_color, box)
        self.logger.debug(f'rover forte percent {percent:.2f}')
        return percent

    def lib_forte_ready(self):
        """True when liberation may be cast: forte >= half, or fallback."""
        if self.ring_index != Elements.WIND:
            return True
        if self.is_forte_full():
            self.lib_hold_since = -1
            return True
        percent = self.forte_percent()
        if percent >= self.LIB_MIN_FORTE:
            self.lib_hold_since = -1
            return True
        if self.liberation_available():
            if self.lib_hold_since < 0:
                self.lib_hold_since = time.time()
            elif self.time_elapsed_accounting_for_freeze(
                    self.lib_hold_since) > self.LIB_HOLD_TIMEOUT:
                self.logger.info(
                    f'forte gate timed out at {percent:.2f}, casting liberation anyway')
                return True
        self.logger.debug(
            f'hold liberation: forte {percent:.2f} < {self.LIB_MIN_FORTE}')
        return False

    # field preference: his liberation is his biggest aero hit — inside
    # Ciaccona's field it is amplified. Hold it briefly when a field is
    # imminent instead of dumping it unbuffed seconds before one goes up
    # (which also puts it on cd for the field itself).
    CIACCONA_FIELD_DURATION = 18
    CIACCONA_LIB_CD = 20
    FIELD_HOLD_TIMEOUT = 8

    def ciaccona_field_active(self):
        c = self.char_ciaccona
        return (c is not None and getattr(c, 'last_liberation', -1) > 0
                and c.time_elapsed_accounting_for_freeze(c.last_liberation)
                < self.CIACCONA_FIELD_DURATION)

    def ciaccona_field_imminent(self):
        """A field is at most ~one team cycle away: her Q is castable now
        (cast-recency-guarded — the off-field cd cache is stale), or her
        last field just expired and the Q is inside the 18->20s cd tail."""
        c = self.char_ciaccona
        if c is None:
            return False
        if getattr(c, 'last_liberation', -1) > 0:
            elapsed = c.time_elapsed_accounting_for_freeze(c.last_liberation)
            if elapsed < self.CIACCONA_LIB_CD:
                # within her cd: only the tail end counts as imminent
                return elapsed >= self.CIACCONA_FIELD_DURATION
        return c.liberation_available()

    def lib_field_ok(self):
        """Field-preference gate for the liberation, with anti-deadlock."""
        self.decide_teammates()
        if self.char_ciaccona is None or self.in_opener():
            # no field coming / opener: damage now beats waiting
            return True
        if self.ciaccona_field_active():
            self.field_hold_since = -1
            return True
        if not self.ciaccona_field_imminent():
            # next field is a full cd away — don't sit on the lib for it
            self.field_hold_since = -1
            return True
        if self.field_hold_since < 0:
            self.field_hold_since = time.time()
        elif self.time_elapsed_accounting_for_freeze(
                self.field_hold_since) > self.FIELD_HOLD_TIMEOUT:
            self.logger.info('field hold timed out, casting liberation anyway')
            return True
        self.logger.debug('hold liberation: Ciaccona field imminent')
        return False

    def click_liberation_gated(self, build_wait=0.0, **kwargs):
        """Cast liberation only once the forte bar reads >= LIB_MIN_FORTE.

        build_wait > 0: spend up to that long on N1s building forte toward
        the threshold before giving up. Passed only by the full routine —
        never while a teammate is waiting, where holding the field to build
        forte would stall the interlock.

        LIB_WHEN_AVAILABLE bypasses all gating: fire on cooldown.
        """
        if self.LIB_WHEN_AVAILABLE:
            clicked = self.click_liberation(**kwargs)
            if clicked:
                self.lib_hold_since = -1
                self.field_hold_since = -1
            return clicked
        if not self.lib_field_ok():
            return False
        if not self.lib_forte_ready():
            if (build_wait <= 0 or not self.liberation_available()
                    or self.need_fast_perform()):
                return False
            self.logger.debug(f'building forte for liberation, up to {build_wait}s')
            start = time.time()
            while self.time_elapsed_accounting_for_freeze(start) < build_wait:
                if self.lib_forte_ready():
                    break
                self.click(interval=0.1)
                self.check_combat()
                self.task.next_frame()
            if not self.lib_forte_ready():
                self.logger.debug('forte still below threshold, holding liberation')
                return False
        clicked = self.click_liberation(**kwargs)
        if clicked:
            self.lib_hold_since = -1
            self.field_hold_since = -1
        return clicked

    def dodge_cancel(self):
        """Tap-dodge to cancel landing/skill recovery while staying on field.

        Switch-outs cancel backswing for free, so this is used only before
        an on-field follow-up (liberation after the aerial string). Skipped
        while airborne, where a dodge becomes an aerial dash.
        """
        if self.wind_routine_flying():
            return False
        self.continues_right_click(0.05)
        self.sleep(0.05)
        return True

    def perform_wind_routine(self):
        if self.has_intro:
            # ride the intro aerial window, then liberation on the way down
            if self.wind_routine_click_while_flying(2):
                self.click_liberation_gated(send_click=True)
                self.wind_routine_wait_down()
                return
        self.wind_routine_wait_down(check_forte_full=False)
        # echo is an instant key: fire it every visit, not only when the
        # E happens to be off cooldown
        self.click_echo(time_out=0)
        if self.resonance_available() and not self.is_forte_full():
            start = time.time()
            flying = False
            while time.time() - start < 1:
                self.send_resonance_key(interval=0.1)
                self.task.next_frame()
                self.click(interval=0.1)
                if flying := self.wind_routine_flying():
                    break
            if not self.use_skyfall_severance:
                if flying and not self.wind_routine_click_while_flying(1.74):
                    # aerial string ended in a plunge landing: cancel the
                    # landing recovery so liberation comes out immediately
                    self.dodge_cancel()
            else:
                if flying and not self.wind_routine_click_while_flying(1.6):
                    self.dodge_cancel()
                if self.click_resonance(send_click=False)[0]:
                    self.wind_routine_click_while_flying(1)
        # full routine: allowed to spend up to 2s of N1s building the forte
        # bar to the half mark before casting (fast routine never waits)
        self.click_liberation_gated(send_click=True, build_wait=2.0)
        self.wind_routine_wait_down()

    def fast_perform_wind_routine(self):
        """Compressed rotation when Fleurdelys / a MUST teammate is waiting."""
        if self.has_intro:
            if self.wind_routine_click_while_flying(0.5):
                return
        if self.wind_routine_flying():
            self.click_liberation_gated(send_click=True)
            self.wind_routine_wait_down(check_forte_full=False)
            self.sleep(0.03)
        if self.is_forte_full():
            self.send_resonance_key()
            return
        self.click_echo(time_out=0)
        if self.resonance_available() and not self.wind_routine_flying():
            self.send_resonance_key()
            self.sleep(0.1)
        att_time = 1 - (time.time() - self.last_perform)
        if att_time > 0 and self.wind_routine_flying():
            self.wind_routine_click_while_flying(att_time)
        if self.use_skyfall_severance:
            self.click_resonance(send_click=False)
        if self.click_liberation_gated(send_click=True):
            self.sleep(0.03)
        if self.is_forte_full():
            self.send_resonance_key()

    def wind_routine_click_while_flying(self, duration, interval=0.1):
        start = time.time()
        while time.time() - start < duration:
            if not self.wind_routine_flying():
                return False
            self.click(interval=0.1)
            self.sleep(interval)
        return True

    def wind_routine_flying(self):
        if self.task.has_lavitator:
            return self.flying()
        elif self.current_resonance() > 0.15:
            return True

    def wind_routine_wait_down(self, check_forte_full=True):
        if self.wind_routine_flying():
            if self.task.has_lavitator:
                self.wait_down()
            else:
                self.task.wait_until(lambda: self.current_resonance() < 0.15,
                                     post_action=lambda: self.click(interval=0.1, after_sleep=0.01),
                                     time_out=2.5)
        if check_forte_full:
            self.sleep(0.03)
            if self.is_forte_full():
                self.send_resonance_key()
        else:
            self.sleep(0.01)
        return True

    # ------------------------------------------------- other rover variants

    def perform_spectro_routine(self):
        if self.has_intro:
            self.continues_normal_attack(1)
        self.wait_down()
        self.spectro_routine_aftertune_combo()
        self.click_echo(time_out=0)
        if self.is_forte_full():
            self.check_combat()
            if self.resonance_available() and self.click_resonance()[0]:
                self.continues_normal_attack(1.4)
                self.sleep(0.1)
        self.check_combat()
        if not self.click_liberation_gated(send_click=True):
            self.click_resonance()

    def spectro_routine_aftertune_combo(self):
        self.heavy_attack()
        self.sleep(0.4)
        self.continues_normal_attack(0.7)

    def perform_havoc_routine(self):
        self.wait_down()
        self.heavy_click_forte(check_fun=self.is_mouse_forte_full)
        self.click_liberation_gated(send_click=True)
        if self.click_resonance(send_click=True)[0]:
            return
        if not self.click_echo():
            self.click()
        self.continues_normal_attack(1.1 - self.time_elapsed_accounting_for_freeze(self.last_switch_time))

    def perform_basic_routine(self):
        if self.has_intro:
            self.continues_normal_attack(self.intro_motion_freeze_duration + 0.2)
        self.wait_down()
        self.click_echo()
        liber = self.click_liberation_gated(send_click=True)
        res = self.click_resonance(send_click=True)[0]
        if not (liber or res):
            self.continues_normal_attack(1)


rover_wind_forte_color = {  # aero forte bar fill (wind green); anchored on the
    'r': (60, 120),         # codebase wind ring / Ciaccona forte ranges —
    'g': (200, 255),        # calibrate against `rover forte percent` in the
    'b': (150, 215)         # debug log if readings sit near 0 or near 1
}
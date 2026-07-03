import time

from src.char.BaseChar import BaseChar, SwitchPriority


class ShoreKeeper(BaseChar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outrotime = -1
        self.dodge_count = 0
        self.attribute = 0
        self._last_forte_spend = 0.0
        self._outro_retry_until = 0.0

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
        # Armed comeback (user: 'switch out and then switch back until her
        # outro is applied'): claim the field back so the near-full ring is
        # verified/finished while it is still warm. Placed after the scripted
        # block so the opener's ordering still wins while it drives.
        if time.time() < self._outro_retry_until:
            return SwitchPriority.MUST
        self.decide_teammate()
        current_name = current_char.char_name if current_char else None
        if self.attribute == 2 and has_intro and current_name in {'Augusta', 'char_augusta'}:
            # REFRESH-NEED gating (measured, log 3a0c1e77): the unconditional
            # claim pulled her in for three no-op visits (15.8s of a 25s lib cd
            # window) while Iuno's amp bank was the actual blocker. Claim only
            # when her buff genuinely needs refreshing, placed EARLY in the cd
            # window so Iuno's amp can land last:
            try:
                from src.char.Augusta import augusta_lib_remaining
                from src.combat.BuffTracker import get_buff_tracker, SK_OUTRO
                t = augusta_lib_remaining(self.task)
                rem = get_buff_tracker(self.task).remaining(SK_OUTRO)
                if t is None or rem < t + 11.0:
                    # emergency / cold start: the buff would be under the 4s
                    # burst-gate margin at the majesty gate (~t+7) -- refresh.
                    return SwitchPriority.MUST
                if rem < t + 17.0 and t >= 10.0:
                    # routine refresh, early in the window (payoff END sits at
                    # ~t+17: the gate plus the ~9s burst routine).
                    return SwitchPriority.MUST
                # else: nothing to add -- fall through to the economy cede.
            except Exception:
                return SwitchPriority.MUST   # never break the cycle on a read error
        # ANTI-BOUNCE (user footage: SK -> Iuno -> SK within seconds): after SK
        # leaves the field, she must not take it right back from IUNO -- that
        # slot belongs to Augusta (the amp receiver). The bounce happened when
        # SK's exit was a PLAIN swap (unconfirmed full), Iuno's short visit
        # ended low-con, and the engine's generic "refresh an unbuffed support"
        # rule picked the just-departed SK. Her MUST claim above still wins
        # (Augusta's con-full exit), so the cycle itself is unaffected.
        if (current_name in {'Iuno', 'char_iuno'}
                and 0 <= self.time_elapsed_accounting_for_freeze(self.last_switch_time) < 6):
            return SwitchPriority.NO
        # FIELD-TIME ECONOMY (user: 'shorekeeper is wasting field time, only
        # switch to her when necessary'): while BOTH her contributions are
        # comfortably live -- the Stellarealm field and her outro amp -- a
        # visit adds nothing the team needs; cede the slot (NO) so Augusta and
        # Iuno keep the field. When either drops inside the refresh margin she
        # competes normally again, fields, and refreshes it (her lib is
        # front-loaded on every visit). Her MUST claim on Augusta's con-full
        # exit is checked ABOVE, so the +1-Majesty outro chain and the burst
        # cycle are unaffected; cold start (tracker never saw her lib) is
        # unaffected too.
        try:
            from src.combat.BuffTracker import (get_buff_tracker,
                                                SK_LIBERATION, SK_OUTRO)
            tracker = get_buff_tracker(self.task)
            if (tracker.has(SK_LIBERATION)
                    and tracker.remaining(SK_LIBERATION) > 10
                    and tracker.remaining(SK_OUTRO) > 10):
                return SwitchPriority.NO
        except Exception:
            self.logger.debug('SK field-time economy check failed', exc_info=True)
        return super().get_switch_priority(current_char, has_intro, target_low_con)

    def skip_combat_check(self):
        return self.has_intro or self.flying()

    def _cast_liberation_now(self):
        """Cast her Resonance Liberation the instant it is ready, so the team buff
        it applies goes up immediately -- she was delaying it behind filler basics
        / a 2.2s attack, so the buff was still down while the cast was available.
        click_liberation no-ops when it is on cooldown. On success the
        Stellarealm is stamped on the
        buff tracker (fixed 30s field that persists with her off-field) so the
        rotation can read its live remaining. Returns True if it fired.

        RETRY (measured, log 3a0c1e77): the first lib click reliably failed
        ('clicked liberation but no effect' -- intro/echo recovery eating the
        key press) and the refresh then drifted 3-4.5s before anything
        retried. One settle-and-retry claws that back; genuine on-cooldown
        calls stay a single cheap check."""
        for attempt in (0, 1):
            if self.click_liberation():
                from src.combat.BuffTracker import get_buff_tracker, SK_LIBERATION
                get_buff_tracker(self.task).apply(SK_LIBERATION, source=self)
                return True
            if attempt == 0 and not self.has_cd('liberation'):
                self.sleep(0.35)
                continue
            break
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

    def _finish_pending_outro(self):
        """Comeback visit for a near-full exit (user: 'switch out and then
        switch back until her outro is applied'). Re-reads the ring: consumed
        -> the outro applied on the way out, nothing to do; still partial ->
        finish it and leave via the outro. Returns True when this visit was
        spent on the retry (caller should return)."""
        from src.combat.StrictRotation import topoff_concerto, confirm_con_full
        con = self.get_current_con()
        if con < 0.05:
            self.logger.info('ShoreKeeper: outro applied on the way out '
                             '(ring consumed) -- retry cleared')
            self._outro_retry_until = 0.0
            return False
        self.logger.info(f'ShoreKeeper: outro retry -- ring still {con:.2f}, '
                         f'finishing it')
        if (topoff_concerto(self, self.NEAR_FULL_FILL_BUDGET,
                            allow_early_switch=False)
                or confirm_con_full(self)):
            self._outro_retry_until = 0.0
            self.switch_next_char(free_intro=True)
            return True
        # not confirmed full yet -- keep the claim armed and try again next
        # visit while the window lasts
        return False

    def _do_perform_default(self):
        if self.has_intro:
            self._intro_wait()
        if (time.time() < self._outro_retry_until
                and self._finish_pending_outro()):
            return
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
        self.spend_forte()
        self.switch_next_char()

    # The GOLD blaze of her full forte bar, measured from video frames of the
    # actual full state (density in the sample box: full 0.20-0.38, everything
    # else <=0.03). The blue-channel cap is what excludes the dim gray-white
    # dashes of an uncharged bar and the near-white forte_white_color glyph.
    FORTE_GOLD = {
        'r': (170, 255),
        'g': (140, 255),
        'b': (20, 160)
    }

    def _forte_bar_glowing(self):
        """Colour fallback measured for HER bar: the generic is_forte_full
        checks NEAR-WHITE (244+/246+/250+) pixels, but SK's full bar blazes
        GOLD -- the white check reads ~0 on it, which is why the first
        fallback never fired either (log aeadc5b2: zero forte events again).

        The box samples the RIGHT END of the bar BODY, not the end-cap glyph
        the generic forte_full box uses: frame measurements showed the blaze
        peters out right where that glyph box starts (density 0.081 there,
        under any safe threshold, and Iuno's bar cap reads the same 0.08).
        Position doubles as the full-vs-partial discriminator -- her segments
        fill left to right and never reach this region until full (measured
        0.000-0.008 partial vs 0.20+ full), so colour alone not telling
        99% from 100% doesn't matter here. Drawn on the overlay as
        forte_gold_<pct> for live verification."""
        box = self.task.box_of_screen_scaled(3840, 2160, 2110, 1996, 2210, 2036,
                                             name='forte_gold', hcenter=True)
        percent = self.task.calculate_color_percentage(self.FORTE_GOLD, box)
        return percent > 0.12

    def _forte_read_suppressed(self):
        """True while inside the post-spend backoff window (see
        FORTE_SPEND_BACKOFF): the just-spent bar's fading blaze must not be
        re-read as a fresh full."""
        return time.time() - self._last_forte_spend < self.FORTE_SPEND_BACKOFF

    def is_forte_full(self):
        """Generic-name override so every generic forte site (try_spend_forte's
        default check, build_concerto's spend during outro top-offs) sees HER
        bar: the base white-glyph check reads ~0 on her GOLD blaze, which
        silently disabled all those sites for her."""
        if self._forte_read_suppressed():
            return False
        return bool(self._forte_bar_glowing() or super().is_forte_full())

    def is_mouse_forte_full(self):
        """Template-first with colour fallbacks (user report: 'sk doesn't hold
        mouse click to spend forte when its full').

        The mouse_forte TEMPLATE ships as a 1920-wide capture and upscales 2x
        blurrily on a 4K frame (FeatureSet load: original_width:1920,
        scale_x:2.0), where the 0.6-threshold match can chronically miss. The
        colour channels (gold blaze + generic white via the is_forte_full
        override above) back it up. All read empty on an uncharged bar, so no
        false spends are added (heavy_click_forte's hold also self-terminates
        if the gauge check drops)."""
        if self._forte_read_suppressed():
            return False
        return bool(super().is_mouse_forte_full() or self.is_forte_full())

    # Illation hold tuning (user: 'sk hold attack dodge cancel should be a bit
    # longer, the attack doesnt go off'). The base heavy_click_forte releases
    # the instant the gauge check reads not-full and the old spend cancelled
    # 0.05s later -- a single washed frame of the gold blaze released the hold
    # before the heavy charged, and even a clean hold got its hit cancelled.
    FORTE_HOLD_MIN = 0.6       # keep the button down at least this long
    FORTE_CANCEL_DELAY = 0.5   # let the released hit register before moving on
    # Suppress full reads this long after a spend: the blaze FADES over ~a
    # second after the held heavy, and re-reading that fade as 'full again'
    # chained back-to-back phantom holds (~3s each: 2s drain-wait + min hold +
    # cancel settle) that burned the whole outro top-off budget without one
    # build action (user: forte spending 'is prevent[ing] her from getting
    # full concerto'). The bar takes far longer than this to genuinely refill,
    # so nothing real is suppressed.
    FORTE_SPEND_BACKOFF = 4.0

    def heavy_click_forte(self, check_fun=None):
        """Base hold + a MINIMUM hold time: release only once the gauge reads
        drained AND FORTE_HOLD_MIN has elapsed, so a single flicker frame of
        the blaze cannot cut the hold short of the charge."""
        check_fun = check_fun or self.is_mouse_forte_full
        if not check_fun():
            return None
        start = time.time()
        self.task.mouse_down()
        success = self.task.wait_until(lambda: not check_fun(), time_out=2)
        remaining = self.FORTE_HOLD_MIN - (time.time() - start)
        if remaining > 0:
            self.sleep(remaining)
        self.task.mouse_up()
        self.sleep(0.05)
        return success

    def spend_forte(self, check=None):
        """Illation: when her forte bar is full, HOLD the mouse until the
        gauge drains (heavy_click_forte holds by design), then settle
        FORTE_CANCEL_DELAY so the released hit actually registers (user: 'the
        attack doesnt go off'). No dodge cancel afterwards (user: 'remove sk
        dodge cancels') -- the recovery plays out naturally. try_spend_forte
        routes through this automatically.

        BANKED during the 1st rotation (user: 'in the 1st rotation, dont let
        sk fbreak'): while the scripted opener drives, the forte is never
        spent -- every spend site funnels through this method (the basic
        string's forte_check, sk_open2's heavy slot, _spend_skill_and_forte,
        build_concerto's spend rung), so one gate here holds the charge until
        the reactive phase takes over."""
        from src.combat.VariableRotation import get_active_rotation
        if get_active_rotation(self.task).is_active():
            self.logger.info('ShoreKeeper: forte banked (no spend during the '
                             '1st rotation)')
            return False
        held = self.heavy_click_forte(check or self.is_mouse_forte_full)
        if held is not None:
            # give the released heavy its impact frames before anything else
            self.sleep(self.FORTE_CANCEL_DELAY)
            # arm the backoff so the fading blaze is not re-read as a fresh
            # full and chained into another (phantom) hold
            self._last_forte_spend = time.time()
        return held

    def _bind_augusta_outro(self):
        """Register SK as the receiver of Augusta's outro amp when this intro
        consumed it. The buff (15% amp; +1 Majesty for Augusta when SK performs
        her OWN outro while carrying it) expires the moment SK is switched off,
        and the tracker's on_char_switch_out models exactly that -- so its
        remaining() stays honest for anything reading it. Guarded: a transient
        read error must never break the intro."""
        try:
            if self.has_intro and self.check_outro() in {'Augusta', 'char_augusta'}:
                from src.combat.BuffTracker import get_buff_tracker, AUGUSTA_OUTRO
                get_buff_tracker(self.task).bind_receiver(AUGUSTA_OUTRO,
                                                          self.char_name)
        except Exception:
            self.logger.debug('ShoreKeeper: bind_augusta_outro skipped', exc_info=True)

    def _intro_wait(self):
        self._bind_augusta_outro()
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
            # 3. lib, SKILL (Chaos Theory), ba123, ha. Measured across logs
            # c5607282 and 3a0c1e77: the skill grants ~20 concerto instantly
            # and the REMAINDER TRICKLES IN OFF-FIELD (kit: cast-and-leave),
            # so casting it HERE lets the trickle run through beats 3-5 for
            # free -- saving it for sk_open2 forfeited exactly that (she
            # re-entered beat 6 at the same 0.32 she left with). Echo stays
            # saved for sk_open2 (its concerto is instant either way).
            self._cast_liberation_now()
            self.click_resonance()
            # forte_check: spend her enhanced heavy the moment it charges during
            # the basics (it is a big concerto source) instead of letting it
            # overcap until the next scripted heavy.
            basic_attacks(self, 3, forte_check=self.is_mouse_forte_full, cancel=agg)
            self._heavy_unless_banked()
        elif beat.name == 'sk_open2':
            # 7. lib-check (cd skip ~0.1s), echo, forte heavy, outro top-off.
            # Echo was saved by sk_open; the forte heavy follows IMMEDIATELY
            # (no skill cast before it -- the skill is on its ~16s cd from
            # beat 2, and the old skill-then-heavy order left a measured 1.11s
            # animation-settle gap). No scripted basics either: the outro
            # top-off supplies exactly as many as the remaining gap needs.
            self._cast_liberation_now()
            self.click_echo(time_out=0)
            if not self.spend_forte():
                self._heavy_unless_banked()
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

    def _heavy_unless_banked(self):
        """Scripted heavy slot, forte-safe during the opener: at full forte a
        held heavy IS Illation (heavy_attack holds the mouse 0.6s), so with the
        1st-rotation forte ban active a charged bar swaps the heavy for two
        basics instead of breaking the bank (user: 'in the 1st rotation, dont
        let sk fbreak'). Outside the scripted rotation, or with the bar not
        full, the normal heavy fires."""
        from src.combat.VariableRotation import get_active_rotation
        from src.combat.StrictRotation import basic_attacks
        if (get_active_rotation(self.task).is_active()
                and self.is_mouse_forte_full()):
            self.logger.info('ShoreKeeper: heavy slot skipped (forte banked); '
                             'basics instead')
            basic_attacks(self, 2)
            return
        self.heavy_attack()

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
        self.spend_forte()

    # A swap-out with concerto in (0.7, 1.0) must not waste the ring (user:
    # 'she should switch out and then switch back until her outro is
    # applied'). She LEAVES anyway -- if the ring was genuinely full and only
    # misread, the outro fires on that very swap -- and arms a comeback claim
    # (OUTRO_RETRY_WINDOW). On the return visit the ring is re-read: consumed
    # -> the outro applied on the way out, done; still partial -> finish it
    # and leave via the outro. The window bounds the ping-pong if the ring
    # never confirms.
    NEAR_FULL_HOLD_MIN = 0.7
    NEAR_FULL_FILL_BUDGET = 6.0
    OUTRO_RETRY_WINDOW = 15.0

    def switch_next_char(self, *args, **kwargs):
        # Heavy before every switch-out (user: 'make sk do heavy attack before
        # switching') -- extra concerto/damage, and the swap cancels the
        # recovery. At full forte outside the opener, spend_forte fires the
        # PROPER held Illation (settle + backoff bookkeeping); otherwise
        # _heavy_unless_banked plays the plain heavy, downgrading to basics
        # only when the 1st-rotation forte bank must not be broken.
        if not self.spend_forte():
            self._heavy_unless_banked()
        # Reactive-phase outro hardening (no-op while the scripted rotation
        # drives). Her outro buff is REQUIRED by the cycle -- Augusta must carry
        # it into her burst -- and 0.8s of plain basics barely moved her ring
        # (log: swapped out at 0.89/0.74 con). Build with her real concerto
        # sources instead (echo/skill/lib via the aggressive top-off) and never
        # early-bail (mandatory), then leave via a forced outro when full.
        from src.combat.VariableRotation import reactive_outro_topoff
        from src.combat.BuffTracker import get_buff_tracker, SK_OUTRO
        reactive_outro_topoff(self, kwargs, threshold=0.6, aggressive=True,
                              mandatory=True)
        con = self.get_current_con()
        self.logger.info(f'ShoreKeeper switch-out: concerto={con:.2f}')
        if (not kwargs.get('free_intro')
                and self.NEAR_FULL_HOLD_MIN < con < 1.0):
            # leave now; if the ring was genuinely full the outro fires on
            # this swap -- then come back and verify (see _finish_pending_outro)
            self._outro_retry_until = time.time() + self.OUTRO_RETRY_WINDOW
            self.logger.info(f'ShoreKeeper: leaving at {con:.2f} -- armed the '
                             f'switch-back to finish the outro')
        tracker = get_buff_tracker(self.task)
        # Leaving the field ends any receiver-bound buff riding on her (Augusta's
        # outro amp) -- outro or plain swap alike, per the kit's 'expires on
        # swap'. The in-game +1 Majesty for Augusta already resolved if this exit
        # is the outro; the tracker just stops counting the amp.
        tracker.on_char_switch_out(self.char_name)
        if kwargs.get('free_intro') or self.is_con_full():
            self.outrotime = time.time()
            self.dodge_count = 5
            # 15% team amp + recovery butterflies; per the kit map it SURVIVES
            # switching, so it is duration-only (~29s tuned window) -- stamped
            # here so Augusta's burst gate reads its live remaining.
            tracker.apply(SK_OUTRO, source=self)
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

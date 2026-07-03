import time

from src.char.BaseChar import BaseChar, SwitchPriority


class ShoreKeeper(BaseChar):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.outrotime = -1
        self.dodge_count = 0
        self.attribute = 0

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

    def dodge_cancel(self):
        """Tap-dodge to cut a cast's landing/recovery (e.g. the Stellarealm
        deploy) when grounded, so the next action starts sooner. Ported from the
        reactive custom build; the built-in already dodges in this exact state
        (auto_dodge), so it does not knock her out of anything she needs."""
        if not self.flying():
            self.continues_right_click(0.05)
            self.sleep(0.05)

    def _cast_liberation_now(self):
        """Cast her Resonance Liberation the instant it is ready, so the team buff
        it applies goes up immediately -- she was delaying it behind filler basics
        / a 2.2s attack, so the buff was still down while the cast was available.
        click_liberation no-ops when it is on cooldown, and the dodge cuts the
        Stellarealm-deploy recovery. On success the Stellarealm is stamped on the
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
                self.dodge_cancel()
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

    def _do_perform_default(self):
        if self.has_intro:
            self._intro_wait()
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
        self.heavy_click_forte(self.is_mouse_forte_full)
        self.switch_next_char()

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
            # saved for sk_open2 (its concerto is instant either way). The
            # dodge cuts the skill's recovery.
            self._cast_liberation_now()
            self.click_resonance()
            self.dodge_cancel()
            # forte_check: spend her enhanced heavy the moment it charges during
            # the basics (it is a big concerto source) instead of letting it
            # overcap until the next scripted heavy.
            basic_attacks(self, 3, forte_check=self.is_mouse_forte_full, cancel=agg)
            self.heavy_attack()
        elif beat.name == 'sk_open2':
            # 7. lib-check (cd skip ~0.1s), echo, forte heavy, outro top-off.
            # Echo was saved by sk_open; the forte heavy follows IMMEDIATELY
            # (no skill cast before it -- the skill is on its ~16s cd from
            # beat 2, and the old skill-then-heavy order left a measured 1.11s
            # animation-settle gap). No scripted basics either: the outro
            # top-off supplies exactly as many as the remaining gap needs.
            self._cast_liberation_now()
            self.click_echo(time_out=0)
            if not self.heavy_click_forte(self.is_mouse_forte_full):
                self.heavy_attack()
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
        self.heavy_click_forte(self.is_mouse_forte_full)

    def switch_next_char(self, *args, **kwargs):
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

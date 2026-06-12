import time  # noqa
from enum import IntEnum, StrEnum  # noqa
from typing import Any  # noqa

import cv2  # noqa
import numpy as np  # noqa

from ok import Config, Logger  # noqa
from src import text_white_color  # noqa

SKILL_TIME_OUT = 15


class CharType(StrEnum):
    MAIN_DPS = 'MainDps'
    SUB_DPS = 'SubDps'
    HEALER = 'Healer'


class SwitchPriority(StrEnum):
    NORMAL = 'normal'
    MUST = 'must'
    NO = 'no'


class Elements(IntEnum):
    SPECTRO = 0
    ELECTRIC = 1
    FIRE = 2
    ICE = 3
    WIND = 4
    HAVOC = 5


Role = CharType
role_values = [role for role in CharType]

DEFAULT_BUFF_TIME_BY_TYPE = {
    CharType.MAIN_DPS: 0,
    CharType.SUB_DPS: 14,
    CharType.HEALER: 24,
}


def get_default_buff_time(char_type=CharType.MAIN_DPS):
    return DEFAULT_BUFF_TIME_BY_TYPE.get(CharType(char_type or CharType.MAIN_DPS), 0)


class BaseChar:
    """Base class for game characters.

    AI editing guide:
    - Character subclasses usually override ``do_perform`` for the on-field rotation.
    - Use helper methods such as ``click_resonance``, ``click_liberation``,
      ``click_echo``, ``heavy_attack``, ``continues_normal_attack``, and
      ``switch_next_char`` instead of sending raw keys directly.
    - Keep loops bounded by timeouts and call ``self.task.next_frame()`` or
      ``self.sleep(...)`` while waiting for UI state changes.
    - ``char_type`` and ``buff_time`` are configured by CharFactory; custom code
      should not hard-code team role unless the character's mechanics require it.
    """

    def __init__(self, task, index, char_name=None, confidence=1, ring_index=-1, char_type=CharType.MAIN_DPS,
                 buff_time=None):
        self.white_off_threshold = 0.01
        self.task = task
        self.sleep_adjust = 0
        self.char_name = char_name
        self.index = index
        self.ring_index = ring_index
        self.last_switch_time = -1
        self.last_switch_in_time = -1
        self.last_res = -1
        self.last_echo = -1
        self.last_liberation = -1
        self.has_intro = False
        self.has_sub_dps_intro = False
        self.is_current_char = False
        self._liberation_available = False
        self._resonance_available = False
        self._echo_available = False
        self.full_ring_area = 0
        self.last_perform = 0
        self.current_con = 0
        self.has_tool_box = False
        self.intro_motion_freeze_duration = 0.9
        self.last_outro_time = -1
        self.confidence = confidence
        self._buff_time = 0
        self._buff_time_configured = False
        self.set_char_type(char_type)
        self.set_buff_time(buff_time)
        self.last_buff_time = -1
        self.logger = Logger.get_logger(self.name)
        self.check_f_on_switch = True
        self.cycle_start_time = 0.0
        self.cycle_time_out = 1.1
        self.cycle_intro_time = 1.2

    def set_char_type(self, char_type=CharType.MAIN_DPS):
        self._char_type = CharType(char_type or CharType.MAIN_DPS)
        if not self._buff_time_configured:
            self._buff_time = get_default_buff_time(self.char_type)

    def get_char_type(self):
        return self._char_type

    @property
    def char_type(self):
        return self.get_char_type()

    def set_buff_time(self, buff_time=None):
        self._buff_time_configured = buff_time is not None
        self._buff_time = get_default_buff_time(self.char_type) if buff_time is None else float(buff_time)

    @property
    def type(self):
        return self.char_type

    @property
    def is_healer(self):
        return self.char_type == CharType.HEALER

    @property
    def is_main_dps(self):
        return self.char_type == CharType.MAIN_DPS

    @property
    def is_sub_dps(self):
        return self.char_type == CharType.SUB_DPS

    def get_buff_time(self):
        return self._buff_time

    @property
    def buff_time(self):
        return self.get_buff_time()

    def has_buff(self):
        return self.buff_time > 0 and self.last_buff_time > 0 and (
                self.time_elapsed_accounting_for_freeze(self.last_buff_time) < self.buff_time)

    def cycle(self):
        self.cycle_start()
        while self.time_elapsed_accounting_for_freeze(
                self.cycle_start_time) < self.cycle_time_out + self.cycle_intro_time:
            if self.do_cycle():
                self.cycle_sleep()
                continue
            else:
                break
        self.switch_next_char()

    def do_cycle(self):
        return

    def cycle_start(self):
        self.cycle_start_time = time.time()

    def cycle_sleep(self, duration=0.1):
        to_sleep = duration - (time.time() - self.cycle_start_time)
        self.sleep(to_sleep)

    def flying_based_on_resonance(self):
        if not self.has_cd('resonance') and not self.task.box_highlighted('resonance'):
            return True

    def skip_combat_check(self):
        return False

    def use_tool_box(self):
        if self.has_tool_box:
            self.task.send_key(self.task.key_config['Tool Key'])
            self.has_tool_box = False

    @property
    def name(self):
        return f"{self.__class__.__name__}"

    def __eq__(self, other):
        if isinstance(other, BaseChar):
            return self.name == other.name and self.index == other.index
        return False

    def perform(self):
        self.last_perform = time.time()
        self.do_perform()
        self.logger.debug(f'set current char false {self.index}')

    def wait_down(self, click=True):
        if not self.task.has_lavitator and self.has_intro:
            if click:
                self.continues_normal_attack(self.intro_motion_freeze_duration)
            else:
                self.sleep(self.intro_motion_freeze_duration)
        else:
            start = time.time()
            while self.flying() and time.time() - start < 2.5:
                if click:
                    self.task.click(interval=0.2)
                else:
                    self.sleep(0.2)
                self.task.next_frame()

    def wait_intro(self, time_out=1.2, click=True):
        if self.has_intro:
            self.task.wait_until(self.down, post_action=self.click_with_interval if click else None, time_out=time_out)

    def down(self):
        return (self.current_resonance() > 0 and not self.has_cd('resonance')) or (
                self.current_liberation() > 0 and not self.has_cd('liberation'))

    def click_with_interval(self, interval=0.1):
        self.click(interval=interval)

    def click(self, *args: Any, **kwargs: Any):
        kwargs['down_time'] = 0.01
        self.task.click(*args, **kwargs)

    def do_perform(self):
        self.wait_intro(1.2)
        self.click_echo(time_out=0)
        self.click_liberation()
        if not self.click_resonance()[0]:
            self.heavy_click_forte(self.is_mouse_forte_full)
        self.switch_next_char()

    def has_cd(self, box_name):
        return self.task.has_cd(box_name)

    def is_available(self, percent, box_name):
        return percent == 0 or not self.has_cd(box_name)

    def switch_out(self, con_full=False):
        self.last_switch_time = time.time()
        self.is_current_char = False
        self.has_intro = False
        self.has_sub_dps_intro = False
        if con_full or self.current_con == 1:
            if self.buff_time > 0:
                self.last_buff_time = self.last_switch_time
            self.logger.info('switch_out at full con set current_con to 0')
            self.current_con = 0

    def __repr__(self):
        return self.__class__.__name__

    def switch_next_char(self, post_action=None, free_intro=False, target_low_con=False):
        self.is_forte_full()
        self.has_intro = False
        self.has_sub_dps_intro = False
        self._liberation_available = self.liberation_available()
        self.use_tool_box()
        self.task.switch_next_char(self, post_action=post_action, free_intro=free_intro,
                                   target_low_con=target_low_con)

    def sleep(self, sec, check_combat=True):
        if not check_combat:
            self.task.skip_combat_check = True
        self.task.sleep(sec)
        self.task.skip_combat_check = False

    def alert_skill_failed(self):
        self.task.log_error('Click skill failed, check if the keybinding is correct in ok-ww settings!',
                            notify=True)
        self.task.screenshot('click_resonance too long, breaking')

    def click_resonance(self, post_sleep=0, has_animation=False, send_click=True, animation_min_duration=0,
                        check_cd=False, time_out=0):
        clicked = False
        self.logger.debug('click_resonance start')
        last_click = 0
        last_op = 'click'
        resonance_click_time = 0
        start = time.time()
        animation_start = 0
        the_time_out = time_out if time_out != 0 else SKILL_TIME_OUT
        while True:
            if time.time() - start > the_time_out:
                self.task.in_liberation = False
                if the_time_out == 0:
                    self.alert_skill_failed()
                break
            elif self.task.in_liberation and time.time() - start > 6:
                self.task.in_liberation = False
                break
            if has_animation:
                if not self.task.in_team()[0]:
                    self.task.in_liberation = True
                    animation_start = time.time()
                    the_time_out = SKILL_TIME_OUT
                    if time.time() - resonance_click_time > 6:
                        self.task.in_liberation = False
                        self.logger.error('resonance animation too long, breaking')
                    self.task.next_frame()
                    self.check_combat()
                    continue
                elif self.task.in_liberation:
                    self.task.in_liberation = False
                    self.logger.debug('click_resonance animated break')
                    break

            self.check_combat()
            now = time.time()
            if not self.resonance_available() and (
                    not has_animation or now - start > animation_min_duration):
                self.logger.debug('click_resonance not available break')
                break

            if now - last_click > 0.1:
                if send_click and last_op == 'resonance':
                    self.task.click()
                    last_op = 'click'
                    continue
                if self.resonance_available():
                    if resonance_click_time == 0:
                        clicked = True
                        resonance_click_time = now
                        self.record_resonance_use()
                    last_op = 'resonance'
                    self.send_resonance_key()
                    if has_animation:
                        self.sleep(0.2, check_combat=False)
                last_click = now
            self.task.next_frame()
        self.task.in_liberation = False
        if clicked:
            self.sleep(post_sleep)
        duration = time.time() - resonance_click_time if resonance_click_time != 0 else 0
        if animation_start > 0:
            self.add_freeze_duration(resonance_click_time, time.time() - animation_start)
        self.logger.debug(f'click_resonance end clicked {clicked} duration {duration} animated {animation_start > 0}')
        return clicked, duration, animation_start > 0

    def send_resonance_key(self, post_sleep=0, interval=-1, down_time=0.01):
        self._resonance_available = False
        self.task.send_key(self.get_resonance_key(), interval=interval, down_time=down_time, after_sleep=post_sleep)

    def send_echo_key(self, after_sleep=0, interval=-1, down_time=0.01):
        self._echo_available = False
        self.task.send_key(self.get_echo_key(), interval=interval, down_time=down_time, after_sleep=after_sleep)

    def heavy_click_forte(self, check_fun=None):
        if check_fun is None:
            check_fun = self.is_forte_full
        if check_fun():
            self.task.mouse_down()
            success = self.task.wait_until(lambda: not check_fun(), time_out=2)
            self.task.mouse_up()
            self.sleep(0.05)
            return success

    def send_liberation_key(self, after_sleep=0, interval=-1, down_time=0.01):
        self._liberation_available = False
        self.task.send_key(self.get_liberation_key(), interval=interval, down_time=down_time, after_sleep=after_sleep)

    def record_resonance_use(self):
        self.last_res = time.time()

    def record_liberation_use(self):
        self.last_liberation = time.time()

    def record_echo_use(self):
        self.last_echo = time.time()

    def click_echo(self, duration=0, sleep_time=0, time_out=1):
        if time_out == 0 and self.echo_available():
            self.send_echo_key()
            self.record_echo_use()
            self.logger.debug('click echo')
            return True
        if self.task.is_open_world_auto_combat() and self.ring_index == Elements.FIRE:
            self.logger.debug('open world do not use motorcycle echo')
            return False
        self.logger.debug(f'click_echo start duration: {duration}')
        if self.has_cd('echo'):
            self.logger.debug('click_echo has cd return ')
            return False
        clicked = False
        start = time.time()
        last_click = 0
        time_out += duration
        while True:
            if time.time() - start > time_out:
                self.logger.info("click_echo time out")
                return False
            if not self.echo_available() and (duration == 0 or not clicked):
                break
            now = time.time()
            if duration > 0 and start != 0:
                if now - start > duration:
                    break
            if now - last_click > 0.1:
                if not clicked:
                    self.record_echo_use()
                    clicked = True
                self.send_echo_key()
                last_click = now
            if now - start > SKILL_TIME_OUT:
                self.logger.error(f'click_echo too long {clicked}')
                self.alert_skill_failed()
                break
            self.task.next_frame()
        self.logger.debug(f'click_echo end {clicked}')
        return clicked

    def is_open_world_auto_combat(self):
        return self.task.is_open_world_auto_combat()

    def check_combat(self):
        self.task.check_combat()

    def reset_state(self):
        self.has_intro = False
        self.has_sub_dps_intro = False
        self.current_con = 0
        self.has_tool_box = False
        self._liberation_available = False
        self._echo_available = False
        self._resonance_available = False

    def click_liberation(self, con_less_than=-1, send_click=False, wait_if_cd_ready=0.1):
        if not self.task.use_liberation:
            return False
        if con_less_than > 0 and self.get_current_con() > con_less_than:
            return False
        self.logger.debug('click_liberation start')
        start = time.time()
        last_click = 0
        clicked = False
        if not self.task.in_liberation:
            while self.liberation_available():
                self.logger.debug('click_liberation liberation_available click')
                if send_click:
                    self.click(interval=0.1)
                now = time.time()
                if now - last_click > 0.1:
                    self.send_liberation_key()
                    if not clicked:
                        clicked = True
                    last_click = now
                if time.time() - start > SKILL_TIME_OUT:
                    self.alert_skill_failed()
                    self.task.raise_not_in_combat('too long clicking a liberation')
                self.task.next_frame()
            if clicked:
                if self.task.wait_until(lambda: not self.task.in_team()[0], time_out=0.4,
                                        post_action=self.click_with_interval):
                    self.task.in_liberation = True
                    self.logger.debug('not in_team successfully casted liberation')
                else:
                    self.task.in_liberation = False
                    self.logger.error('clicked liberation but no effect')
                    return False
            else:
                start = time.time()
                while not self.has_cd('liberation') and time.time() - start < wait_if_cd_ready:
                    self.send_liberation_key(after_sleep=0.05)
                    if self.task.wait_until(lambda: not self.task.in_team()[0], time_out=0.1):
                        self.task.in_liberation = True
                        self.logger.debug('not in_team successfully casted liberation')
                if not self.task.in_liberation:
                    return False
        start = time.time()
        while not self.task.in_team()[0]:
            self.task.in_liberation = True
            if not clicked:
                clicked = True
            if send_click:
                self.click(interval=0.1)
            if time.time() - start > 7:
                self.task.in_liberation = False
                self.task.raise_not_in_combat('too long a liberation, the boss was killed by the liberation')
            self.task.next_frame()
        duration = time.time() - start
        self.add_freeze_duration(start, duration)
        self.record_liberation_use()
        self.task.in_liberation = False
        self._liberation_available = False
        if clicked:
            self.logger.info(f'click_liberation end {duration}')
        return clicked

    def on_combat_end(self, chars):
        pass

    def add_freeze_duration(self, start, duration=-1.0, freeze_time=0.1):
        self.task.add_freeze_duration(start, duration, freeze_time)

    def time_elapsed_accounting_for_freeze(self, start, intro_motion_freeze=False):
        return self.task.time_elapsed_accounting_for_freeze(start, intro_motion_freeze)

    def get_liberation_key(self):
        return self.task.get_liberation_key()

    def get_echo_key(self):
        return self.task.get_echo_key()

    def get_resonance_key(self):
        return self.task.get_resonance_key()

    def get_switch_priority(self, current_char=None, has_intro=False, target_low_con=False):
        return SwitchPriority.NORMAL

    def resonance_available(self):
        return self.available('resonance', check_color=False)

    def available(self, box, check_color=True, check_cd=True):
        if self.is_current_char:
            return self.task.available(box, check_color=check_color, check_cd=check_cd)
        else:
            return not self.task.has_cd(box, self.index)

    def echo_available(self):
        return self.available('echo', check_color=False)

    def extra_action_available(self):
        return self.available('extra_action', check_color=True, check_cd=False)

    def is_con_full(self):
        if self.current_con == 1:
            return True
        return self.task.is_con_full()

    def get_current_con(self):
        if self.current_con == 1:
            return 1
        self.current_con = self.task.get_current_con()
        return self.current_con

    def is_mouse_forte_full(self):
        return self.task.find_mouse_forte()

    def is_e_forte_full(self):
        return self.task.find_e_forte()

    def is_forte_full(self):
        box = self.task.box_of_screen_scaled(3840, 2160, 2251, 1993, 2311, 2016, name='forte_full', hcenter=True)
        white_percent = self.task.calculate_color_percentage(forte_white_color, box)
        box.confidence = white_percent
        self.task.draw_boxes('forte_full', box)
        return white_percent > 0.08

    def liberation_available(self, check_color=True):
        return self.available('liberation', check_color=check_color)

    def __str__(self):
        return self.__repr__()

    def normal_attack_until_can_switch(self):
        self.task.click()
        while self.time_elapsed_accounting_for_freeze(self.last_perform) < 1.1:
            self.task.click(interval=0.1)

    def need_fast_perform(self):
        current_char = self.task.get_current_char(raise_exception=False) if hasattr(self.task, 'get_current_char') else self
        for char in getattr(self.task, 'chars', []):
            if char is None or char == current_char:
                continue
            if char.get_switch_priority(current_char=current_char, has_intro=False,
                                        target_low_con=False) == SwitchPriority.MUST:
                self.logger.info(f'In lock with {char}')
                return True
        return False

    def wait_switch_cd(self):
        since_last_switch = self.time_elapsed_accounting_for_freeze(self.last_perform)
        if since_last_switch <= 1:
            self.logger.debug(f'wait_switch_cd {since_last_switch}')
            self.continues_normal_attack(1 - since_last_switch)

    def continues_normal_attack(self, duration, interval=0.1, after_sleep=0, click_resonance_if_ready_and_return=False,
                                until_con_full=False):
        start = time.time()
        while time.time() - start < duration:
            if click_resonance_if_ready_and_return and self.resonance_available():
                return self.click_resonance()
            if until_con_full and self.is_con_full():
                return
            self.task.click()
            self.sleep(interval)
        self.sleep(after_sleep)

    def continues_click(self, key, duration, interval=0.1):
        start = time.time()
        while time.time() - start < duration:
            self.task.send_key(key, interval=interval)

    def continues_right_click(self, duration, interval=0.1, direction_key=None):
        if direction_key is not None:
            self.task.send_key_down(direction_key)
            self.task.next_frame()
        start = time.time()
        while time.time() - start < duration:
            self.task.click(interval=interval, key="right")
        if direction_key is not None:
            self.task.send_key_up(direction_key)

    def normal_attack(self):
        self.logger.debug('normal attack')
        self.check_combat()
        self.task.click()

    def heavy_attack(self, duration=0.6):
        self.check_combat()
        self.logger.debug('heavy attack start')
        self.task.mouse_down()
        self.sleep(duration)
        self.task.mouse_up()
        self.sleep(0.01)
        self.logger.debug('heavy attack end')

    def current_resonance(self):
        return self.task.calculate_color_percentage(text_white_color,
                                                    self.task.get_box_by_name('box_resonance'))

    def current_echo(self):
        return self.task.calculate_color_percentage(text_white_color,
                                                    self.task.get_box_by_name('box_echo'))

    def current_liberation(self):
        return self.task.calculate_color_percentage(text_white_color, self.task.get_box_by_name('box_liberation'))

    def flying(self):
        if not self.task.has_lavitator:
            return False
        percent = self.task.calculate_color_percentage(text_white_color, self.task.get_box_by_name('edge_levitator'))
        return percent < 0.1

    def check_outro(self):
        if not self.has_intro:
            return 'null'
        latest_time = 0
        outro = 'null'
        for char in self.task.chars:
            if char == self:
                continue
            if char.last_switch_time > latest_time:
                latest_time = char.last_switch_time
                outro = char.char_name
        self.logger.info(f'erned outro from {outro}')
        return outro

    def is_first_engage(self):
        result = (0 <= self.last_perform - self.task.combat_start < 0.1)
        if result:
            self.logger.info('first engage')
        return result

    def wait_switch(self):
        return False

    def switch_other_char(self):
        target_index = next(
            (char.index for char in self.task.chars if char and char.is_healer and char.index != self.index),
            (self.index + 1) % len(self.task.chars)
        )
        next_char = str(target_index + 1)

        from src.task.AutoCombatTask import AutoCombatTask
        if isinstance(self.task, AutoCombatTask):
            self.logger.debug('AutoCombatTask, skip switch_other_char')
            return
        self.logger.debug(f'{self.char_name} on_combat_end {self.index} switch next char: {next_char}')
        start = time.time()
        while time.time() - start < 6:
            in_team, current_index, count = self.task.in_team()
            if in_team and current_index != self.index:
                for char in self.task.chars:
                    if char:
                        char.is_current_char = (char.index == current_index)
                break
            else:
                self.task.send_key(next_char)
            self.sleep(0.2, False)
        self.logger.debug(f'switch_other_char on_combat_end {self.index} switch end')

    def has_long_action(self):
        return self.task.find_one(self.task.get_target_names()[0], box='box_target_enemy_long', threshold=0.6)

    def has_long_action2(self):
        return self.task.find_one(self.task.get_target_names()[0], box='target_box_long2', threshold=0.6)

    def f_break(self, check_f_on_switch=False):
        if check_f_on_switch and not self.check_f_on_switch:
            return
        self.task.f_break()


forte_white_color = {
    'r': (244, 255),
    'g': (246, 255),
    'b': (250, 255)
}

dot_color = {
    'r': (195, 255),
    'g': (195, 255),
    'b': (195, 255)
}

import time

from qfluentwidgets import FluentIcon

from ok import TriggerTask, Logger, TaskDisabledException
from src.char.CharFactory import char_names
from src.task.BaseCombatTask import BaseCombatTask, NotInCombatException, CharDeadException

logger = Logger.get_logger(__name__)


class AutoCombatTask(BaseCombatTask, TriggerTask):
    """Trigger task that runs each character's combat script while in combat.

    Tolerates frames where the current character can't be identified
    (switch/revive flicker), retries once when a character script raises
    unexpectedly, and publishes per-character rotation stats to the info
    panel.
    """

    auto_target_configurable = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.default_config = {
            '_enabled': True,
            'Auto Target': True,
            'Use Liberation': True,
            'Check Levitator': True,
        }
        self.trigger_interval = 0.1
        self.name = "Auto Combat"
        self.description = "Enable auto combat in Abyss, Game World etc"
        self.icon = FluentIcon.CALORIES
        self.last_is_click = False
        self.config_description = {
            'Auto Target': 'Turn off to enable auto combat only when manually target enemy using middle click',
            'Use Liberation': 'Do not use Liberation in Open World to Save Time',
            'Check Levitator': 'Toggle the levitator and verify if the character is floating',
        }
        self.op_index = 0
        self.char_features_warmed_up = False
        self._slot_stats = {}
        self._slot_total = 0
        self._stats_start = 0.0
        self._last_info_update = 0.0

    def warm_up_char_features(self):
        if self.char_features_warmed_up:
            return
        try:
            for char_name in char_names:
                self.get_feature_by_name(char_name)
        except Exception as e:
            logger.warning(f'warm_up_char_features failed: {e}')
            return
        self.char_features_warmed_up = True
        logger.info(f'warm_up_char_features loaded {len(char_names)} character templates')

    # ----- instrumentation -----

    def _reset_slot_stats(self):
        self._slot_stats = {}
        self._slot_total = 0
        self._stats_start = time.time()
        self._last_info_update = 0.0

    def _record_slot(self, char_name, duration):
        count, total = self._slot_stats.get(char_name, (0, 0.0))
        self._slot_stats[char_name] = (count + 1, total + duration)
        self._slot_total += 1
        now = time.time()
        if now - self._last_info_update < 1:
            return
        self._last_info_update = now
        elapsed = max(now - self._stats_start, 0.001)
        parts = [f'{name} {t / c:.2f}s x{c}' for name, (c, t) in self._slot_stats.items()]
        self.info_set('Slot Avg', ' | '.join(parts))
        self.info_set('Switches/min', round(self._slot_total / elapsed * 60))
        self.info_set('Combat Time', int(elapsed))

    # ----- robustness -----

    def _reacquire_current_char(self, time_out=2):
        """Current char unidentifiable (switch frame, revive flicker):
        re-sync instead of crashing. Returns the char or None."""
        start = time.time()
        while time.time() - start < time_out:
            self.next_frame()
            if not self.in_team()[0]:
                return None
            self.load_chars()
            char = self.get_current_char(raise_exception=False)
            if char is not None:
                return char
            self.sleep(0.05)
        logger.warning('could not re-acquire current char')
        return None

    def run(self):
        self.warm_up_char_features()
        ret = False
        if not self.scene.in_team(self.in_team_and_world):
            return ret
        self.use_liberation = self.config.get('Use Liberation')
        if not self.use_liberation and not self.in_world():  # open world only
            self.use_liberation = True
        combat_start = time.time()
        last_error_char = None
        while self.in_combat():
            if not ret:
                ret = True
                self._reset_slot_stats()
            char = self.get_current_char(raise_exception=False)
            if char is None:
                char = self._reacquire_current_char()
                if char is None:
                    continue  # let in_combat() decide if combat ended
            slot_start = time.time()
            try:
                char.perform()
                self._record_slot(char.name, time.time() - slot_start)
                last_error_char = None
            except CharDeadException:
                self.log_error('Characters dead', notify=True)
                break
            except NotInCombatException as e:
                logger.info(f'auto_combat_task_out_of_combat {int(time.time() - combat_start)} {e}')
                break
            except TaskDisabledException:
                raise
            except Exception as e:
                slot_time = time.time() - slot_start
                logger.error(f'char code error in {char.name} after {slot_time:.2f}s in slot', e)
                if last_error_char == char.name:
                    raise  # same char failing twice in a row: real bug, surface it
                last_error_char = char.name
                self.load_chars()  # re-sync and give the rotation one retry
        if ret:
            self.combat_end()
        return ret

    def realm_perform(self):
        if not self.last_is_click:
            if self.op_index % 10 == 0:
                self.send_key_and_wait_animation('4', self.in_illusive_realm, enter_animation_wait=0.2)
            else:
                self.click()
        else:
            if self.available('liberation'):
                self.send_key_and_wait_animation(self.get_liberation_key(), self.in_illusive_realm)
            elif self.available('echo'):
                self.send_key(self.get_echo_key())
            elif self.available('resonance'):
                self.send_key(self.get_resonance_key())
            elif self.is_con_full() and self.in_team()[0]:
                self.send_key_and_wait_animation('2', self.in_illusive_realm)
        self.last_is_click = not self.last_is_click
        self.op_index += 1
        self.sleep(0.02)


from ok import run_task
from config import config

if __name__ == "__main__":
    run_task(config, task=AutoCombatTask, debug=True)

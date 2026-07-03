import time

from qfluentwidgets import FluentIcon

from src.task.BaseCombatTask import BaseCombatTask
from ok import Logger
from src.task.WWOneTimeTask import WWOneTimeTask

logger = Logger.get_logger(__name__)


class DiagnosisTask(WWOneTimeTask, BaseCombatTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.group_name = "Diagnosis"
        self.group_icon = FluentIcon.UNIT
        self.description = "Diagnosis Problem, Performance Test, Run in Game World"
        self.name = "Diagnosis"

    def run(self):
        super().run()
        if not self.in_team()[0]:
            self.log_error('must be in game world and in teams, please check you game resolution is 16:9', notify=True)
            return
        self.load_hotkey(force=True)

        capture_cost = 0
        ocr_cost = 0
        while True:
            self.load_chars()
            char = self.get_current_char()

            if not char:
                self.info.clear()
                self.info['Current Character'] = "None"
            else:
                start = time.time()
                self.reset_scene()
                self.next_frame()
                capture_cost += time.time() - start
                start = time.time()
                self.refresh_cd()
                ocr_cost += time.time() - start
                self.info['Capture Frame Count'] = self.info.get('Capture Frame Count', 0) + 1
                self.info['Capture Frame Rate'] = round(
                    self.info['Capture Frame Count'] / (capture_cost or 1),
                    2)
                self.info['OCR'] = ocr_cost / self.info['Capture Frame Count']
                self.info['Game Resolution'] = f'{self.frame.shape[1]}x{self.frame.shape[0]}'
                self.info['Current Character'] = str(char)
                self.info['Resonance CD'] = self.get_cd('resonance')
                self.info['Echo CD'] = self.get_cd('echo')
                self.info['Liberation CD'] = self.get_cd('liberation')
                self.info['Concerto'] = char.get_current_con()

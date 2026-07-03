# BaseEchoInventoryTask.py
import os
import time

from ok import FindFeature, Logger
from src.task.BaseWWTask import BaseWWTask

logger = Logger.get_logger(__name__)


class BaseEchoInventoryTask(BaseWWTask, FindFeature):
    """背包声骸批处理任务的共用骨架 (批量强化 / 批量修改主属性)。

    子类需实现 is_0_level() 判断当前选中声骸是否未处理过,
    并在 run() 循环中先调用 _select_next_echo() 选中下一个声骸。
    """

    def find_echo_enhance(self):
        return self.ocr(0.82, 0.86, 0.97, 0.96, match='培养')

    def _select_next_echo(self, done_message):
        """点击培养进入下一个待处理声骸。

        没有可处理的声骸时输出汇总日志并返回 False (调用方应结束任务);
        成功进入培养界面则返回 True。
        """
        enhance = self.find_echo_enhance()
        if not enhance:
            raise Exception('必须在背包声骸界面过滤后开始!')
        current_level = self.is_0_level()
        if not current_level:
            total = self.info_get('成功声骸数量') + self.info_get('失败声骸数量')
            if self.debug:
                self.screenshot('无可强化声骸')
            self.log_info(f'{done_message}, 任务结束! 强化{total}个, 符合条件{self.info_get("成功声骸数量")}个',
                          notify=True)
            if self.info_get('成功声骸数量') >= 1:
                try:
                    os.startfile(os.path.abspath("screenshots"))
                except Exception as e:
                    self.log_error(f"无法打开截图文件夹: {e}")
            return False
        start = time.time()
        while time.time() - start < 5:
            if enhance:
                self.click(enhance, after_sleep=0.5)
            enhance = self.find_echo_enhance()
            if not enhance:
                break
        return True

from qfluentwidgets import FluentIcon

from ok import Logger
from src.task.DomainTask import DomainTask

logger = Logger.get_logger(__name__)


class ForgeryTask(DomainTask):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.icon = FluentIcon.FLAG
        self.name = 'Forgery Challenge'
        self.description = 'Farms the selected Forgery Challenge. Must be able to teleport (F2).'
        self.support_schedule_task = True
        self.default_config = {
            'Which Forgery Challenge to Farm': 1,  # starts with 1
        }
        self.config_description = {
            'Which Forgery Challenge to Farm': 'The Forgery Challenge number in the F2 list.',
        }
        self.stamina_once = 40
        self.total_number = 15

    def run(self):
        super().run()
        self.make_sure_in_world()
        self.farm_forgery()

    def farm_forgery(self, daily=False, used_stamina=0, config=None):
        if daily:
            must_use = 180 - used_stamina
        else:
            must_use = 0
        if config is None:
            config = self.config
        serial = config.get('Which Forgery Challenge to Farm', 1)

        def teleport_once():
            self.teleport_into_domain(serial)

        self.farm_domain_with_recovery_loop(must_use, teleport_once)

    def teleport_into_domain(self, serial_number):
        self.open_boss_book('ningsu')
        self.info_set('Teleport to Forgery Challenge', serial_number - 1)
        if serial_number > self.total_number:
            raise IndexError(f'Index out of range, max is {self.total_number}')
        self.click_on_book_target(serial_number, self.total_number)
        self.wait_click_travel()
        self.wait_in_team_and_world(time_out=self.teleport_timeout)
        self.sleep(1)
        self.walk_until_f(time_out=2)
        for _ in range(5):
            self.pick_f()
            if self.wait_click_feature('gray_button_challenge', relative_x=4, raise_if_not_found=False,
                                       click_after_delay=1, threshold=0.6, after_sleep=1, time_out=3):
                self.click_relative(0.93, 0.90, after_sleep=1)
                self.wait_in_team_and_world(time_out=self.teleport_timeout)
                return
        raise RuntimeError('Failed to enter Forgery Challenge')

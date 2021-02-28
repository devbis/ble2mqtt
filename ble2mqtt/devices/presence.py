import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from bleak.backends.device import BLEDevice

from .base import BINARY_SENSOR_DOMAIN, Sensor

logger = logging.getLogger(__name__)


@dataclass
class SensorState:
    presence: bool = False
    last_check: datetime = None


class Presence(Sensor):
    NAME = 'presence'
    SENSOR_CLASS = SensorState
    SUPPORT_PASSIVE = True
    SUPPORT_ACTIVE = False
    MANUFACTURER = 'Generic'
    THRESHOLD = 120  # if no activity more than THRESHOLD, consider presence=OFF
    PASSIVE_SLEEP_INTERVAL = 1
    SEND_DATA_PERIOD = 60

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = None

    @property
    def entities(self):
        return {
            BINARY_SENSOR_DOMAIN: [
                {
                    'name': 'presence',
                    'device_class': 'presence',
                },
            ],
        }

    def handle_advert(self, scanned_device: BLEDevice, *args, **kwargs):
        self._state = self.SENSOR_CLASS(
            presence=True,
            last_check=datetime.now(),
        )
        logger.debug(
            f'Advert received for {self}, current state: {self._state}',
        )

    async def handle_passive(self, *args, **kwargs):
        self.last_sent_value = None
        self.last_sent_time = None
        await super().handle_passive(*args, **kwargs)

    async def do_passive_loop(self, publish_topic):
        if self._state.presence and \
                self._state.last_check + \
                timedelta(seconds=self.THRESHOLD) < datetime.now():
            self._state.presence = False
        # send if changed or update value every SEND_DATA_PERIOD secs
        if self.last_sent_value is None or \
                self.last_sent_value != self._state.presence or \
                (datetime.now() - self.last_sent_time).seconds > \
                self.SEND_DATA_PERIOD:

            logger.debug(f'Try publish {self._state}')
            await self._notify_state(publish_topic)
            self.last_sent_value = self._state.presence
            self.last_sent_time = datetime.now()

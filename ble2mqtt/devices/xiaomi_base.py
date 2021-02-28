import logging

from ..protocols.xiaomi import XiaomiPoller
from .base import SENSOR_DOMAIN

logger = logging.getLogger(__name__)


class XiaomiHumidityTemperature(XiaomiPoller):
    SENSOR_CLASS = None
    # send data only if temperature or humidity is set
    REQUIRED_VALUES = ('temperature', 'humidity')

    @property
    def entities(self):
        return {
            SENSOR_DOMAIN: [
                {
                    'name': 'temperature',
                    'device_class': 'temperature',
                    'unit_of_measurement': '\u00b0C',
                },
                {
                    'name': 'humidity',
                    'device_class': 'humidity',
                    'unit_of_measurement': '%',
                },
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                },
            ],
        }

    async def read_and_send_data(self, publish_topic):
        battery = await self._read_with_timeout(self.BATTERY_CHAR)
        data_bytes = await self._stack.get()
        # clear queue
        while not self._stack.empty():
            self._stack.get_nowait()
        self._state = self.SENSOR_CLASS.from_data(data_bytes, battery)
        await self._notify_state(publish_topic)

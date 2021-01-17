import json
import logging

from ..protocols.xiaomi import XiaomiPoller
from .base import SENSOR_DOMAIN

logger = logging.getLogger(__name__)


class XiaomiHumidityTemperature(XiaomiPoller):
    SENSOR_CLASS = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = None

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

    async def _notify_state(self, publish_topic):
        logger.info(f'[{self}] send state={self._state}')
        state = {}
        for sensor_name, value in (
                ('temperature', self._state.temperature),
                ('humidity', self._state.humidity),
                ('battery', self._state.battery),
        ):
            if any(
                    x['name'] == sensor_name
                    for x in self.entities.get('sensor', [])
            ):
                if sensor_name != 'battery' or value:
                    state[sensor_name] = self.transform_value(value)

        if state:
            await publish_topic(
                topic='/'.join((self.unique_id, 'state')),
                value=json.dumps(state),
            )

    async def read_and_send_data(self, publish_topic):
        battery = await self._read_with_timeout(self.BATTERY_CHAR)
        data_bytes = await self._stack.get()
        # clear queue
        while not self._stack.empty():
            self._stack.get_nowait()
        self._state = self.SENSOR_CLASS.from_data(data_bytes, battery)
        await self._notify_state(publish_topic)

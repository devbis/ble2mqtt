import asyncio as aio
import json
import logging
import struct
import uuid
from dataclasses import dataclass

from .base import SENSOR_DOMAIN, Device

logger = logging.getLogger(__name__)

DEVICE_NAME = uuid.UUID('00002a00-0000-1000-8000-00805f9b34fb')
FIRMWARE_VERSION = uuid.UUID('00002a26-0000-1000-8000-00805f9b34fb')
MAIN_DATA = uuid.UUID('70BC767E-7A1A-4304-81ED-14B9AF54F7BD')


@dataclass
class SensorState:
    battery: int
    dose: float
    dose_rate: float
    temperature: int

    @classmethod
    def from_data(cls, sensor_data):
        flags, dose, dose_rate, pulses, battery, temp = \
            struct.unpack('<BffHbb', sensor_data)
        return cls(
            dose=round(dose, 4),
            dose_rate=round(dose_rate, 4),
            battery=battery,
            temperature=temp,
        )


class AtomFast(Device):
    NAME = 'atomfast'
    DATA_CHAR = MAIN_DATA
    SENSOR_CLASS = SensorState
    CONNECTION_FAILURES_LIMIT = 10
    DEVICE_DROPS_CONNECTION = False
    MANUFACTURER = 'Atom'

    def __init__(self, mac, *args, loop, **kwargs) -> None:
        super().__init__(mac, *args, loop=loop, **kwargs)
        self._state = None
        self._stack = aio.LifoQueue(loop=loop)
        self._model = 'Fast'

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
                    'name': 'dose',
                    'unit_of_measurement': 'mSv',
                    'icon': 'atom',
                },
                {
                    'name': 'dose_rate',
                    'unit_of_measurement': 'μSv/h',
                    'icon': 'atom',
                },
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                },
            ],
        }

    async def get_device_data(self):
        await self.client.start_notify(MAIN_DATA, self.notification_handler)
        version = await self._read_with_timeout(FIRMWARE_VERSION)
        if isinstance(version, (bytes, bytearray)):
            self._version = version.decode().strip('\0')
        name = await self._read_with_timeout(DEVICE_NAME)
        if isinstance(name, (bytes, bytearray)):
            self._model = name.decode().strip('\0')

    def filter_notifications(self, sender):
        # Consider filtering by sender == 0x24
        return True

    def notification_handler(self, sender, data: bytearray):
        logger.debug("Notification: {0}: {1}".format(
            sender,
            ' '.join(format(x, '02x') for x in data),
        ))
        if self.filter_notifications(sender):
            self._state = self.SENSOR_CLASS.from_data(data)

    async def _notify_state(self, publish_topic):
        logger.info(f'[{self}] send state={self._state}')
        state = {'linkquality': self.linkquality}
        for sensor_name, value in (
                ('dose', self._state.dose),
                ('dose_rate', self._state.dose_rate),
                ('temperature', self._state.temperature),
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

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        while True:
            await self.update_device_data(send_config)
            if not self._state:
                await aio.sleep(5)
                continue

            await self._notify_state(publish_topic)
            await aio.sleep(self.RECONNECTION_TIMEOUT)

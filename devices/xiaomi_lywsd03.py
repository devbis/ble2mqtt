import asyncio as aio
import json
import logging
import struct
import uuid
from dataclasses import dataclass

from bleak import BleakClient

from .base import Device

logger = logging.getLogger(__name__)

DEVICE_NAME = uuid.UUID('00002a00-0000-1000-8000-00805f9b34fb')
FIRMWARE_VERSION = uuid.UUID('00002a26-0000-1000-8000-00805f9b34fb')
LYWSD_DATA = uuid.UUID('EBE0CCC1-7A0A-4B0C-8A1A-6FF2997DA3A6')
LYWSD_BATTERY = uuid.UUID('EBE0CCC4-7A0A-4B0C-8A1A-6FF2997DA3A6')


@dataclass
class SensorState:
    battery: int
    temperature: float
    humidity: float

    @classmethod
    def from_data(cls, sensor_data, battery_data):
        t, h, voltage = struct.unpack('<hBH', sensor_data)
        return cls(
            temperature=round(t/100, 2),
            humidity=h,
            battery=int(ord(battery_data)),
        )


class XiaomiHumidityTemperatureLYWSD(Device):
    NAME = 'xiaomilywsd'
    REQUIRE_CONNECTION = True
    RECONNECTION_TIMEOUT = 60

    def __init__(self, mac, *args, loop, **kwargs):
        super().__init__(mac, *args, loop=loop, **kwargs)
        self._state = None
        self._model = None
        self._version = None
        self._stack = aio.LifoQueue(loop=loop)

    @property
    def manufacturer(self):
        return 'Xiaomi'

    @property
    def dev_id(self):
        return self._mac.replace(':', '').lower()

    @property
    def entities(self):
        return {
            'sensor': [
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

    async def get_client(self):
        return BleakClient(self._mac, address_type='public')

    async def get_device_data(self):
        await self.client.start_notify(LYWSD_DATA, self.notification_handler)
        version = await self._read_with_timeout(FIRMWARE_VERSION)
        if isinstance(version, (bytes, bytearray)):
            self._version = version.decode()
        name = await self._read_with_timeout(DEVICE_NAME)
        if isinstance(name, (bytes, bytearray)):
            self._model = name.decode()

    async def _read_with_timeout(self, char, timeout=5):
        try:
            result = await aio.wait_for(
                self.client.read_gatt_char(char),
                timeout=timeout,
                loop=self._loop,
            )
        # except (aio.TimeoutError, AttributeError):
        except Exception as e:
            logger.error(f'{str(e)}: Cannot connect to device')
            result = None
        return result

    async def _notify_state(self, publish_topic):
        logger.info(f'[{self._mac}] send state={self._state}')
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
                state[sensor_name] = self.transform_value(value)

        if state:
            await publish_topic(
                topic='/'.join((self.unique_id, 'state')),
                value=json.dumps(state),
            )

    def notification_handler(self, sender, data):
        logger.debug("Notification: {0}: {1}".format(
            sender,
            ' '.join(format(x, '02x') for x in data),
        ))
        self._stack.put_nowait(data)

    async def handle(self, publish_topic, *args, **kwargs):
        while True:
            try:
                logger.debug(f'Wait {self} for connecting...')
                await aio.wait_for(
                    self.connection_event.wait(),
                    timeout=1,
                )
            except aio.CancelledError:
                return
            except aio.TimeoutError:
                continue

            try:
                logger.debug(f'{self} connected!')
                battery = await self._read_with_timeout(LYWSD_BATTERY)
                data_bytes = await self._stack.get()
                # clear queue
                while not self._stack.empty():
                    self._stack.get_nowait()
                self._state = SensorState.from_data(data_bytes, battery)
            except ValueError as e:
                logger.error(f'Cannot read values {str(e)}')
            else:
                await self._notify_state(publish_topic)
                if await self.connection_event.wait():
                    await self.close()
                    return
                    # await aio.sleep(5)
            await aio.sleep(1)

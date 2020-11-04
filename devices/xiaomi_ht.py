import asyncio as aio
import json
import logging
import uuid
from dataclasses import dataclass

from bleak import BleakClient

from .base import Device

logger = logging.getLogger(__name__)

SERVICE = uuid.UUID('0000180a-0000-1000-8000-00805f9b34fb')

DEVICE_NAME = '00002a00-0000-1000-8000-00805f9b34fb'
MODEL_NUMBER = '00002a24-0000-1000-8000-00805f9b34fb'
SERIAL_NUMBER = '00002a25-0000-1000-8000-00805f9b34fb'
FIRMWARE_VERSION = '00002a26-0000-1000-8000-00805f9b34fb'
HARDWARE_VERSION = '00002a27-0000-1000-8000-00805f9b34fb'
MANUFACTURER_NAME = '00002a29-0000-1000-8000-00805f9b34fb'

LYWSD02_DATA = 'EBE0CCC1-7A0A-4B0C-8A1A-6FF2997DA3A6'
CGG_DATA = '00000100-0000-1000-8000-00805f9b34fb'
MJHT_DATA = uuid.UUID('226caa55-6476-4566-7562-66734470666d')
MJHT_BATTERY = uuid.UUID('00002a19-0000-1000-8000-00805f9b34fb')


@dataclass
class SensorState:
    battery: int
    temperature: float
    humidity: float

    @classmethod
    def from_data(cls, sensor_data, battery_data):
        t, h = tuple(
            float(x.split('=')[1])
            for x in sensor_data.decode().strip('\0').split(' ')
        )
        return cls(
            temperature=t,
            humidity=h,
            battery=int(ord(battery_data)),
        )


class XiaomiHumidityTemperature(Device):
    NAME = 'xiaomihtv1'
    REQUIRE_CONNECTION = True
    RECONNECTION_TIMEOUT = 60

    def __init__(self, loop, mac, *args, **kwargs):
        super().__init__(loop, *args, **kwargs)
        self._mac = mac
        self.client = BleakClient(mac, address_type='public')

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
                    'unit_of_measurement': 'ºC',
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

    async def init(self):
        version = await self._read_with_timeout(FIRMWARE_VERSION)
        if isinstance(version, (bytes, bytearray)):
            self._version = version.decode()
        name = await self._read_with_timeout(DEVICE_NAME)
        if isinstance(name, (bytes, bytearray)):
            self._model = name.decode()

    async def _read_with_timeout(self, char):
        try:
            result = await aio.wait_for(
                aio.ensure_future(
                    self.client.read_gatt_char(char),
                    loop=self._loop,
                ),
                timeout=5,
                loop=self._loop,
            )
        # except (aio.TimeoutError, AttributeError):
        except Exception as e:
            logger.exception(f'{str(e)}: Cannot connect to device')
            result = None
        return result

    async def _notify_state(self, publish_topic):
        logger.info(f'[{self._mac}] send state {self._state=}')
        coros = []
        for sensor_name, value in (
                ('temperature', self._state.temperature),
                ('humidity', self._state.humidity),
                ('battery', self._state.battery),
        ):
            if any(
                    x['name'] == sensor_name
                    for x in self.entities.get('sensor', [])
            ):
                coros.append(
                    publish_topic(
                        topic='/'.join((self.unique_id, sensor_name)),
                        value=json.dumps({
                            sensor_name: self.transform_value(value)
                        }),
                    )
                )
        await aio.gather(*coros)

    def notification_handler(self, sender, data):
        logger.debug("Notification: {0}: {1}".format(
            sender,
            ' '.join(format(x, '02x') for x in data),
        ))
        # sender is 0xd or several requests it becomes
        # /org/bluez/hci0/dev_58_2D_34_32_E0_69/service000c/char000d
        if sender == 0xd or isinstance(sender, str) and sender.endswith('000d'):
            # b'T=23.6 H=39.6\x00'
            self._stack.put_nowait(data)

    async def handle(self, publish_topic, *args, **kwargs):
        # TODO: subscribe to advertisement ?
        await self.client.start_notify(MJHT_DATA, self.notification_handler)
        while True:
            try:
                logger.info(f'Wait {self} for connecting...')
                await aio.wait_for(
                    self.connection_event.wait(),
                    timeout=30,
                )
                logger.info(f'{self} connected!')
                battery = await self._read_with_timeout(MJHT_BATTERY)
                data_bytes = await self._stack.get()
                # clear queue
                while not self._stack.empty():
                    self._stack.get_nowait()
                self._state = SensorState.from_data(data_bytes, battery)

            except ValueError as e:
                logger.exception(f'Cannot read values {str(e)}')
            else:
                await self._notify_state(publish_topic)
                if await self.client.is_connected():
                    await aio.sleep(5)
            await aio.sleep(1)

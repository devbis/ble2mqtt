import asyncio as aio
import logging
import struct
import typing as ty
import uuid
from dataclasses import dataclass

from .base import SENSOR_DOMAIN, ConnectionMode, Sensor

_LOGGER = logging.getLogger(__name__)

DEVICE_MODE_UUID = uuid.UUID('00001a00-0000-1000-8000-00805f9b34fb')
DATA_UUID = uuid.UUID('00001a01-0000-1000-8000-00805f9b34fb')
FIRMWARE_UUID = uuid.UUID('00001a02-0000-1000-8000-00805f9b34fb')

LIVE_MODE_CMD = bytes([0xA0, 0x1F])


@dataclass
class SensorState:
    temperature: float
    moisture: int
    illuminance: ty.Optional[int]
    conductivity: int
    battery: int = 0

    @classmethod
    def from_data(cls, data: bytes, battery: int):
        if len(data) == 24:  # is ropot
            light = None
            temp, moist, conductivity = struct.unpack(
                "<hxxxxxBhxxxxxxxxxxxxxx", data
            )
        else:
            temp, light, moist, conductivity = struct.unpack(
                "<hxIBhxxxxxx", data
            )

        return cls(
            temperature=temp / 10.0,
            moisture=moist,
            conductivity=conductivity,
            illuminance=light,
            battery=battery,
        )


class FlowerMonitorMiFlora(Sensor):
    NAME = 'miflora'
    MANUFACTURER = 'Xiaomi'
    MODEL = 'MiFlora'
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_POLL_WITH_DISCONNECT
    DEFAULT_RECONNECTION_SLEEP_INTERVAL = 300
    READ_DATA_IN_ACTIVE_LOOP = True

    def __init__(self, mac, *args, **kwargs) -> None:
        super().__init__(mac, *args, **kwargs)
        self.RECONNECTION_SLEEP_INTERVAL = int(
            kwargs.get('interval', self.DEFAULT_RECONNECTION_SLEEP_INTERVAL)
        )

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
                    'name': 'moisture',
                    'device_class': 'moisture',
                    'unit_of_measurement': '%',
                },
                {
                    'name': 'illuminance',
                    'device_class': 'illuminance',
                    'unit_of_measurement': 'lx',
                },
                {
                    'name': 'conductivity',
                    'unit_of_measurement': 'µS/cm',
                },
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                    'entity_category': 'diagnostic',
                },
            ],
        }

    async def read_state(self):
        fw_and_bat = await self._read_with_timeout(FIRMWARE_UUID)
        battery = fw_and_bat[0]
        data = await self._read_with_timeout(DATA_UUID)
        for _ in range(5):
            try:
                self._state = SensorState.from_data(data, battery)
            except ValueError as e:
                _LOGGER.warning(f'{self} {repr(e)}')
                await aio.sleep(1)
            else:
                break

    async def get_device_data(self):
        self._model = self.MODEL
        await self.client.write_gatt_char(
            DEVICE_MODE_UUID,
            LIVE_MODE_CMD,
            response=False,
        )
        fw_and_bat = await self._read_with_timeout(FIRMWARE_UUID)
        self._version = fw_and_bat[2:].decode('ascii').strip('\0')

    async def do_active_loop(self, publish_topic):
        try:
            await aio.wait_for(self.read_state(), 5)
            await self._notify_state(publish_topic)
        except (aio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            _LOGGER.exception(f'{self} problem with reading values')

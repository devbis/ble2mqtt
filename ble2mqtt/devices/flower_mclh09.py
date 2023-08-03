import asyncio as aio
import logging
import struct
import uuid
from dataclasses import dataclass

from .base import Sensor, SENSOR_DOMAIN, ConnectionMode
from .uuids import BATTERY


_LOGGER = logging.getLogger(__name__)


@dataclass
class SensorState:
    temperature: float
    moisture: float
    illuminance: int
    battery: int = 0

    @classmethod
    def from_data(cls, data: bytes, battery: bytes):
        temp, _, moisture, illuminance = struct.unpack('<hHHH', data)
        return cls(
            temperature=round(temp / 24.02482269503546, 2),
            moisture=round(moisture / 13, 2),
            illuminance=illuminance * 0.16,
            battery=int.from_bytes(battery, byteorder='little'),
        )


class FlowerMonitorMCLH09(Sensor):
    NAME = 'mclh09'
    MANUFACTURER = 'LifeControl'
    DATA_CHAR = uuid.UUID('55482920-eacb-11e3-918a-0002a5d5c51b')
    BATTERY_CHAR = BATTERY
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
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                    'entity_category': 'diagnostic',
                },
            ],
        }

    async def read_state(self):
        battery = await self._read_with_timeout(self.BATTERY_CHAR)
        data = await self._read_with_timeout(self.DATA_CHAR)
        for _ in range(5):
            try:
                self._state = SensorState.from_data(data, battery)
            except ValueError as e:
                _LOGGER.warning(f'{self} {repr(e)}')
                await aio.sleep(1)
            else:
                break

    async def do_active_loop(self, publish_topic):
        try:
            await aio.wait_for(self.read_state(), 5)
            await self._notify_state(publish_topic)
        except (aio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            _LOGGER.exception(f'{self} problem with reading values')

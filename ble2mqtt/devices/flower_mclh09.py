import asyncio as aio
import logging
import struct
import uuid
from dataclasses import dataclass

from .base import SENSOR_DOMAIN, ConnectionMode, Sensor
from .uuids import BATTERY

_LOGGER = logging.getLogger(__name__)

FLOWER_SENSOR_CHAR = uuid.UUID('55482920-eacb-11e3-918a-0002a5d5c51b')

TEMPERATURE_VALUES = [68.8, 49.8, 24.3, 6.4, 1.0, -5.5, -20.5, -41.0]
TEMPERATURE_READINGS = [1035, 909, 668, 424, 368, 273, 159, 0]
MOISTURE_VALUES = [60.0, 58.0, 54.0, 22.0, 2.0, 0.0]
MOISTURE_READINGS = [1254, 1249, 1202, 1104, 944, 900]
LIGHT_VALUES = [
    175300.0, 45400.0, 32100.0, 20300.0, 14760.0, 7600.0, 1200.0, 444.0,
    29.0, 17.0, 0.0,
]
LIGHT_READINGS = [911, 764, 741, 706, 645, 545, 196, 117, 24, 17, 0]


def _interpolate(raw_value, values, raw_values):
    index = 0
    if raw_value > raw_values[0]:
        index = 0
    elif raw_value < raw_values[-2]:
        index = len(raw_values) - 2
    else:
        while raw_value < raw_values[index + 1]:
            index += 1

    delta_value = values[index] - values[index + 1]
    delta_raw = raw_values[index] - raw_values[index + 1]
    return (
        (raw_value - raw_values[index + 1]) * delta_value / delta_raw +
        values[index + 1]
    )


def calculate_temperature(raw_value):
    return _interpolate(raw_value, TEMPERATURE_VALUES, TEMPERATURE_READINGS)


def calculate_moisture(raw_value):
    moisture = _interpolate(raw_value, MOISTURE_VALUES, MOISTURE_READINGS)

    if moisture > 100.0:
        moisture = 100.0
    if moisture < 0.0:
        moisture = 0.0

    return moisture


def calculate_illuminance(raw_value):
    return _interpolate(raw_value, LIGHT_VALUES, LIGHT_READINGS)


@dataclass
class SensorState:
    temperature: float
    moisture: float
    illuminance: int
    battery: int = 0

    @classmethod
    def from_data(cls, data: bytes, battery: bytes):
        temp_raw, moisture_raw, illuminance_raw = struct.unpack('<HxxHH', data)
        return cls(
            temperature=round(calculate_temperature(temp_raw), 2),
            moisture=round(calculate_moisture(moisture_raw), 2),
            illuminance=int(calculate_illuminance(illuminance_raw)),
            battery=int.from_bytes(battery, byteorder='little'),
        )


class FlowerMonitorMCLH09(Sensor):
    NAME = 'mclh09'
    MANUFACTURER = 'LifeControl'
    DATA_CHAR = FLOWER_SENSOR_CHAR
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

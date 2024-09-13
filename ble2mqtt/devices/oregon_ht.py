import logging
import struct
import uuid
import typing as ty
from dataclasses import dataclass

from .base import (
    SENSOR_DOMAIN,
    ConnectionMode,
    Sensor,
    SubscribeAndSetDataMixin
)
from .uuids import BATTERY, MANUFACTURER, MODEL_NAME, FIRMWARE_VERSION

_LOGGER = logging.getLogger(__name__)


INDOOR_AND_CH1_TO_3_TH_DATA_UUID = uuid.UUID(
    '74e78e10-c6a4-11e2-b7a9-0002a5d5c51b',
)
FIRST_PACKET = 0x01
SECOND_PACKET = 0x82


@dataclass
class SensorState:
    indoor_temperature: ty.Optional[float]
    indoor_temp_min: ty.Optional[float]
    indoor_temp_max: ty.Optional[float]
    outdoor_temperature: ty.Optional[float]
    outdoor_temp_min: ty.Optional[float]
    outdoor_temp_max: ty.Optional[float]
    humidity: ty.Optional[int]
    humidity_min: ty.Optional[int]
    humidity_max: ty.Optional[int]
    battery: int = 0

    @classmethod
    def extract_word(cls, value: int) -> float:
        return round(value / 10, 2) if value != 0x7FFF else None

    @classmethod
    def from_data(cls, data1: bytes, data2: bytes, battery: int):
        (
            temp0, temp1, temp2, temp3, humid0, humid1, humid2, humid3,
            temp_trend, humid_trend, humid0_max, humid0_min, humid1_max,
            humid1_min, humid2_max,
         ) = struct.unpack('<x4h11b', data1)
        (
            humid2_min, humid3_max, humid3_min, temp0_max, temp0_min,
            temp1_max, temp1_min, temp2_max, temp2_min, temp3_max, temp3_min,
        ) = struct.unpack('<x3B8H', data2)
        return cls(
            indoor_temperature=cls.extract_word(temp0),
            indoor_temp_min=cls.extract_word(temp0_min),
            indoor_temp_max=cls.extract_word(temp0_max),
            outdoor_temperature=cls.extract_word(temp1),
            outdoor_temp_min=cls.extract_word(temp1_min),
            outdoor_temp_max=cls.extract_word(temp1_max),
            humidity=humid0 if humid0 != 0x7f else None,
            humidity_min=humid0_min if humid0_min != 0x7f else None,
            humidity_max=humid0_max if humid0_max != 0x7f else None,
            battery=battery,
        )


class OregonScientificWeatherStation(SubscribeAndSetDataMixin, Sensor):
    NAME = 'oregon_weather'
    MANUFACTURER = 'Oregon'
    DATA_CHAR = INDOOR_AND_CH1_TO_3_TH_DATA_UUID
    BATTERY_CHAR = BATTERY
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_POLL_WITH_DISCONNECT
    DEFAULT_RECONNECTION_SLEEP_INTERVAL = 300

    def __init__(self, mac, *args, **kwargs) -> None:
        super().__init__(mac, *args, **kwargs)
        self.RECONNECTION_SLEEP_INTERVAL = int(
            kwargs.get('interval', self.DEFAULT_RECONNECTION_SLEEP_INTERVAL)
        )
        # cache notifications by 1st byte,
        # we need 0x01 and 0x82 to populate state
        self._notification_cache = {}
        self._last_battery = b''

    @property
    def entities(self):
        return {
            SENSOR_DOMAIN: [
                {
                    'name': 'indoor_temperature',
                    'device_class': 'temperature',
                    'unit_of_measurement': '\u00b0C',
                },
                {
                    'name': 'outdoor_temperature',
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
                    'entity_category': 'diagnostic',
                },
            ],
        }

    def filter_notifications(self, sender, data):
        # sender is 0x17 or several requests it becomes
        # /org/bluez/hci0/dev_58_2D_34_32_E0_69/service000c/char0017
        return (
            sender in (0x17, 0x20) or
            isinstance(sender, str) and
            (sender.endswith('0017') or sender.endswith('0020'))
        ) and len(data) == 20

    def process_data(self, data: bytearray):
        if data[0] in {FIRST_PACKET, SECOND_PACKET}:
            self._notification_cache[data[0]] = data
        if len(self._notification_cache) == 2:
            self._state = self.SENSOR_CLASS.from_data(
                self._notification_cache.pop(FIRST_PACKET),
                self._notification_cache.pop(SECOND_PACKET),
                int.from_bytes(self._last_battery, byteorder='little')
                if self._last_battery else 0,
            )

    async def get_device_data(self):
        manufacturer = await self._read_with_timeout(MANUFACTURER)
        if isinstance(manufacturer, (bytes, bytearray)):
            self._manufacturer = manufacturer.decode().strip('\0')
        name = await self._read_with_timeout(MODEL_NAME)
        if isinstance(name, (bytes, bytearray)):
            self._model = name.decode().strip('\0')
        version = await self._read_with_timeout(FIRMWARE_VERSION)
        if isinstance(version, (bytes, bytearray)):
            self._version = version.decode().strip('\0')
        self._last_battery = await self._read_with_timeout(self.BATTERY_CHAR)
        await super().get_device_data()

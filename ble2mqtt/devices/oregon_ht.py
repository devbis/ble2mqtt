import logging
import struct
import typing as ty
import uuid
from collections import defaultdict
from dataclasses import dataclass, field

from .base import (SENSOR_DOMAIN, ConnectionMode, Sensor,
                   SubscribeAndSetDataMixin)
from .uuids import BATTERY, FIRMWARE_VERSION, MANUFACTURER, MODEL_NAME

_LOGGER = logging.getLogger(__name__)

DEVICE_INFORMATION_UUID = uuid.UUID("74e78e02-c6a4-11e2-b7a9-0002a5d5c51b")
INDOOR_AND_CH1_TO_3_TH_DATA_UUID = uuid.UUID(
    '74e78e10-c6a4-11e2-b7a9-0002a5d5c51b',
)
CH4_TO_7_TH_DATA = uuid.UUID('74e78e14-c6a4-11e2-b7a9-0002a5d5c51b')
PRESSURE_DATA = uuid.UUID('74e78e20-c6a4-11e2-b7a9-0002a5d5c51b')

FIRST_PACKET = 0x01
SECOND_PACKET = 0x82
SENDER_0_3 = 0x17
SENDER_4_7 = 0x20
SENDER_8_11 = 0x1a

NONE_BYTE = 0x7f
NONE_WORD = 0x7fff


@dataclass
class Temperature:
    value: ty.Optional[float] = None
    min: ty.Optional[float] = None
    max: ty.Optional[float] = None

    def is_valid(self):
        return any(
            getattr(self, attr) is not None
            for attr in self.__annotations__
        )


@dataclass
class Humidity:
    value: ty.Optional[int] = None
    min: ty.Optional[int] = None
    max: ty.Optional[int] = None

    def is_valid(self):
        return any(
            getattr(self, attr) is not None
            for attr in self.__annotations__
        )


@dataclass
class SensorState:
    temp_trend: ty.Optional[float] = None
    humid_trend: ty.Optional[int] = None
    temperatures: ty.List[Temperature] = field(
        default_factory=lambda: [Temperature() for _ in range(4)])
    humidities: ty.List[Humidity] = field(
        default_factory=lambda: [Humidity() for _ in range(4)])
    pressure: ty.Optional[int] = None
    battery: int = 0

    def is_ready(self):
        return any(
            getattr(self, attr) is not None
            for attr in self.__annotations__
            if attr != 'battery'
        )

    @classmethod
    def extract_byte(cls, value: int) -> ty.Optional[int]:
        return value if value != NONE_BYTE else None

    @classmethod
    def extract_word(cls, value: int) -> ty.Optional[float]:
        return round(value / 10, 2) if value != NONE_WORD else None

    def populate_0_3(self, data1: bytes, data2: bytes):
        (
            temp0, temp1, temp2, temp3, humid0, humid1, humid2, humid3,
            temp_trend, humid_trend, humid0_max, humid0_min, humid1_max,
            humid1_min, humid2_max,
        ) = struct.unpack('<x4h11b', data1)
        self.temperatures[0].value = self.extract_word(temp0)
        self.temperatures[1].value = self.extract_word(temp1)
        self.temperatures[2].value = self.extract_word(temp2)
        self.temperatures[3].value = self.extract_word(temp3)
        self.humidities[0].value = self.extract_byte(humid0)
        self.humidities[1].value = self.extract_byte(humid1)
        self.humidities[2].value = self.extract_byte(humid2)
        self.humidities[3].value = self.extract_byte(humid3)
        self.temp_trend = self.extract_byte(temp_trend)
        self.humid_trend = self.extract_byte(humid_trend)
        self.humidities[0].max = self.extract_byte(humid0_max)
        self.humidities[0].min = self.extract_byte(humid0_min)
        self.humidities[1].max = self.extract_byte(humid1_max)
        self.humidities[1].min = self.extract_byte(humid1_min)
        self.humidities[2].max = self.extract_byte(humid2_max)
        (
            humid2_min, humid3_max, humid3_min, temp0_max, temp0_min,
            temp1_max, temp1_min, temp2_max, temp2_min, temp3_max,
            temp3_min,
        ) = struct.unpack('<x3B8h', data2)
        self.humidities[2].min = self.extract_byte(humid2_min)
        self.humidities[3].max = self.extract_byte(humid3_max)
        self.humidities[3].min = self.extract_byte(humid3_min)
        self.temperatures[0].max = self.extract_word(temp0_max)
        self.temperatures[0].min = self.extract_word(temp0_min)
        self.temperatures[1].max = self.extract_word(temp1_max)
        self.temperatures[1].min = self.extract_word(temp1_min)
        self.temperatures[2].max = self.extract_word(temp2_max)
        self.temperatures[2].min = self.extract_word(temp2_min)
        self.temperatures[3].max = self.extract_word(temp3_max)
        self.temperatures[3].min = self.extract_word(temp3_min)


class OregonScientificWeatherStation(SubscribeAndSetDataMixin, Sensor):
    NAME = 'oregon_weather'
    MANUFACTURER = 'Oregon'
    INDICATION_CHARS = [
        INDOOR_AND_CH1_TO_3_TH_DATA_UUID,
        # CH4_TO_7_TH_DATA,
        # PRESSURE_DATA,
    ]
    BATTERY_CHAR = BATTERY
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_POLL_WITH_DISCONNECT
    DEFAULT_RECONNECTION_SLEEP_INTERVAL = 300
    TEMPERATURE_CONFIG = {
        'name': 'temperature',
        'device_class': 'temperature',
        'unit_of_measurement': '\u00b0C',
    }
    HUMIDITY_CONFIG = {
        'name': 'humidity',
        'device_class': 'humidity',
        'unit_of_measurement': '%',
    }

    def __init__(self, mac, *args, **kwargs) -> None:
        super().__init__(mac, *args, **kwargs)
        self.RECONNECTION_SLEEP_INTERVAL = int(
            kwargs.get('interval', self.DEFAULT_RECONNECTION_SLEEP_INTERVAL)
        )
        self._device_properties = set()
        self._state = SensorState()
        self._notification_cache = defaultdict(dict)

    @property
    def entities(self):
        return {
            SENSOR_DOMAIN: [
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                    'entity_category': 'diagnostic',
                },
            ],
        }

    def entities_with_lqi(self):
        sensor_entities = [
            *self.entities.get(SENSOR_DOMAIN, []),
            self.get_linkquality_description(),
        ]
        return {
            **self.entities,
            SENSOR_DOMAIN: [
                *sensor_entities,
                *[
                    {
                        **self.TEMPERATURE_CONFIG,
                        'name': f'temperature_{i}',
                    }
                    for i, t in enumerate(self._state.temperatures)
                    if t.is_valid()
                ],
                *[
                    {
                        **self.HUMIDITY_CONFIG,
                        'name': f'humidity_{i}',
                    }
                    for i, h in enumerate(self._state.humidities)
                    if h.is_valid()
                ],
                *([
                    {
                        'name': 'pressure',
                        'device_class': 'pressure',
                        'unit_of_measurement': 'kPa',
                    }
                ] if self._state.pressure is not None else []),
            ],
        }

    def filter_notifications(self, sender: int, data: bytes):
        return sender in [SENDER_0_3, SENDER_4_7, SENDER_8_11]

    def process_data(self, data: bytearray, sender: int):
        if data[0] in {FIRST_PACKET, SECOND_PACKET}:
            self._notification_cache[sender][data[0]] = data

        to_drop = []
        for sndr, packets_by_type in self._notification_cache:
            if len(packets_by_type) == 2:
                to_drop.append(sndr)
                if sndr == SENDER_0_3:
                    _LOGGER.warning(
                        f'[{self}] Processing data from {sndr}: '
                        f'{packets_by_type[FIRST_PACKET].hex(" ")} '
                        f'{packets_by_type[SECOND_PACKET].hex(" ")}')
                    self._state.populate_0_3(
                        packets_by_type[FIRST_PACKET],
                        packets_by_type[SECOND_PACKET],
                    )
                elif sndr == SENDER_4_7:
                    pass
                elif sndr == SENDER_8_11:
                    pass
        for sndr in to_drop:
            self._notification_cache.pop(sndr)

    def _get_indication_chars(self):
        # some models don't expose pressure characteristic,
        # we omit it for subscribing
        return [
            ch for ch in self.INDICATION_CHARS
            if any(
                ch == uuid.UUID(cch.uuid)
                for cch in self.client.characteristics
            )
        ]

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
        self._state.battery = (
            await self._read_with_timeout(self.BATTERY_CHAR)
        )[0]
        dev_props = await self._read_with_timeout(DEVICE_INFORMATION_UUID)
        _LOGGER.warning(f'[{self}] Device properties: {dev_props}')
        if dev_props[1]:
            self._device_properties.add('indoor')
        await super().get_device_data()

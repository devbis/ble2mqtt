import asyncio as aio
import logging
import typing as ty
import uuid
from dataclasses import asdict, dataclass
from enum import Enum

from ..compat import get_loop_param
from ..utils import format_binary
from .base import (BUTTON_DOMAIN, SELECT_DOMAIN, SENSOR_DOMAIN, ConnectionMode,
                   Sensor)
from .uuids import DEVICE_NAME

_LOGGER = logging.getLogger(__name__)


VOLTAGE_CHAR = uuid.UUID('0000ffd1-0000-1000-8000-00805f9b34fb')
STATE_CHAR = uuid.UUID('0000ffd2-0000-1000-8000-00805f9b34fb')
TOTAL_USE_CHAR = uuid.UUID('0000ffd3-0000-1000-8000-00805f9b34fb')
VERSION_CHAR = uuid.UUID('0000ffd4-0000-1000-8000-00805f9b34fb')
ERROR_CHAR = uuid.UUID('0000ffd6-0000-1000-8000-00805f9b34fb')

STATE_ENTITY = 'state'
VOLTAGE_ENTITY = 'voltage'
LOW_SPEED_POWER = 'low_speed_power'
RESET_HEPA = 'reset_hepa'


class CleanerStateEnum(Enum):
    UNKNOWN = 'unknown'
    WORKING = 'working'
    CLOSED = 'closed'
    STANDBY = 'standby'
    CHARGING = 'charging'


class SpeedOption(Enum):
    Low = 'Low'
    Medium = 'Medium'
    High = 'High'


class SpeedLvPower(Enum):
    _60W = 2550
    _80W = 3300
    _100W = 4200
    _110W = 4600
    _130W = 4950
    _150W = 5600
    _180W = 6450
    _200W = 7250
    _280W = 9500
    _300W = 10400
    _400W = 14000

    def to_option(self):
        return self.name[1:]

    @classmethod
    def from_option(cls, option: str):
        return cls[f'_{option}']

    @classmethod
    def _missing_(cls, value: int):
        # find closest to the value
        min_dist = None
        closest = None
        for item in cls.__members__.values():
            dist = abs(value - item.value)
            if min_dist is None or min_dist > dist:
                min_dist = dist
                closest = item
        return closest


class ErrorCode(Enum):
    OK = 0x51
    DustUnInstall = 0x54
    BrushBlocked = 0x52
    BrushDischarge = 0x53
    MotorAbnormal = 0x3857
    DustFull = 0x55
    TemperatureHigh = 0x56
    MIFDateout = 0xf9
    Unknown = 0x9999

    @classmethod
    def _missing_(cls, value):
        return cls.Unknown


@dataclass
class CleanerState:
    battery: int
    voltage: int
    total_use_seconds: int
    numeric_display_type: int
    total_use_seconds: int
    total_clean_seconds: int
    hepa_used_seconds: int
    speed_level: SpeedOption
    low_speed_lv_power: SpeedLvPower
    numeric_display_type: int
    state: CleanerStateEnum
    dust_abnormal_value: ErrorCode
    brush_abnormal_value: ErrorCode
    dust_full_abnormal_value: ErrorCode
    temp_high_abnormal_value: ErrorCode

    @property
    def power(self):
        if self.speed_level == SpeedOption.Medium:
            return SpeedLvPower._110W
        if self.speed_level == SpeedOption.High:
            return SpeedLvPower._400W
        return self.low_speed_lv_power

    @property
    def mif_work_duration_left(self):
        mif_max_work_duration = 36000
        if self.hepa_used_seconds < 0:
            return 0
        elif self.hepa_used_seconds == 0:
            return 1.0
        else:
            return max(
                0.0,
                1.0 - (self.hepa_used_seconds / mif_max_work_duration),
            )

    @property
    def error(self):
        if self.dust_abnormal_value != ErrorCode.OK:
            return self.dust_abnormal_value
        if self.brush_abnormal_value != ErrorCode.OK:
            return self.brush_abnormal_value
        if self.dust_full_abnormal_value != ErrorCode.OK:
            return self.dust_full_abnormal_value
        if self.temp_high_abnormal_value != ErrorCode.OK:
            return self.temp_high_abnormal_value

        if not int(self.mif_work_duration_left * 100):
            return ErrorCode.MIFDateout
        return ErrorCode.OK

    @property
    def total_clean_area(self):
        return int(self.total_clean_seconds / 60.0 * 0.8)

    def as_dict(self):
        return {
            **asdict(self),
            'state': self.state.value,
            'speed_level': self.speed_level.value,
            'actual_power': self.power.to_option(),
            'mif_duration_left': int(self.mif_work_duration_left * 100),
            'total_clean_area': self.total_clean_area,
            'error': self.error.name,
        }


class RoidmiCleaner(Sensor):
    NAME = 'roidmi_cleaner'
    VOLTAGE_CHAR = VOLTAGE_CHAR
    STATE_CHAR = STATE_CHAR
    TOTAL_USE_CHAR = TOTAL_USE_CHAR
    ERROR_CHAR = ERROR_CHAR
    ACTIVE_SLEEP_INTERVAL = 1
    RECONNECTION_SLEEP_INTERVAL = 30
    MANUFACTURER = 'Roidmi'
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_KEEP_CONNECTION

    @property
    def entities(self):
        return {
            SENSOR_DOMAIN: [
                {
                    'name': STATE_ENTITY,
                    'icon': 'vacuum',
                },
                # {
                #     'name': VOLTAGE_ENTITY,
                #     'device_class': 'voltage',
                #     'unit_of_measurement': 'V',
                #     'entity_category': 'diagnostic',
                # },
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                    'entity_category': 'diagnostic',
                },
                {
                    'name': 'mif_duration_left',
                    'unit_of_measurement': '%',
                },
                {
                    'name': 'total_use_seconds',
                    'unit_of_measurement': 'S',
                    'icon': 'timer',
                },
                {
                    'name': 'total_clean_seconds',
                    'unit_of_measurement': 'S',
                    'icon': 'timer-alert',
                },
                {
                    'name': 'total_clean_area',
                    'unit_of_measurement': 'm²',
                    'icon': 'ruler-square',
                },
                {
                    'name': 'hepa_used_seconds',
                    'unit_of_measurement': 'S',
                    'icon': 'filter-variant',
                },
                {
                    'name': 'actual_power',
                    'icon': 'lightning-bolt'
                },
                {
                    'name': 'error',
                    'icon': 'alert-circle-outline',
                },
            ],
            SELECT_DOMAIN: [
                {
                    'name': LOW_SPEED_POWER,
                    'topic': LOW_SPEED_POWER,
                    'icon': 'speed',
                    'options': [x.to_option() for x in SpeedLvPower],
                },
            ],
            BUTTON_DOMAIN: [
                {
                    'name': RESET_HEPA,
                    'topic': RESET_HEPA,
                    'entity_category': 'diagnostic',
                    'device_class': 'restart',
                },
            ],
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = None

    @staticmethod
    def _parse_version_data(data: bytes) -> ty.Tuple[str, str]:
        mcu_version = ''
        firmware_version = ''
        if data[0] == 0xa4:
            mcu_version = data[1:7].decode('ascii').strip()
            firmware_version = data[7:13].decode('ascii').strip()
        return mcu_version, firmware_version

    @staticmethod
    def _parse_voltage_data(data: bytes) -> ty.Tuple[int, int]:
        voltage = int.from_bytes(data[1:3], byteorder='big')
        battery = 0
        if len(data) >= 4:
            battery = data[3]
        return voltage, battery

    @staticmethod
    def _parse_usage_data(data: bytes) -> ty.Tuple[int, int, int]:
        total_use_seconds = (data[1] << 16) + (data[2] << 8) + data[3]
        total_clean_seconds = (data[4] << 16) + (data[5] << 8) + data[6]
        hepa_used_seconds = (data[7] << 16) + (data[8] << 8) + data[9]
        return total_use_seconds, total_clean_seconds, hepa_used_seconds

    @staticmethod
    def _parse_state_data(data: bytes) \
            -> ty.Tuple[CleanerStateEnum, SpeedOption, SpeedLvPower, int]:
        state = CleanerStateEnum.UNKNOWN
        speed_level = SpeedOption.Low
        if data[0] == 0xa2:
            state = {
                0x51: CleanerStateEnum.WORKING,
                0x52: CleanerStateEnum.CLOSED,
                0x53: CleanerStateEnum.STANDBY,
                0x54: CleanerStateEnum.CHARGING,
                0x55: CleanerStateEnum.CHARGING,
            }.get(data[1], CleanerStateEnum.UNKNOWN)
            speed_level = {
                0x51: SpeedOption.Low,
                0x52: SpeedOption.Medium,
                0x53: SpeedOption.High,
            }.get(data[2], SpeedOption.Low)
        numeric_display_type = data[3]
        low_speed_lv_power = SpeedLvPower(
            int.from_bytes(data[4:6], byteorder='big'),
        )
        return state, speed_level, low_speed_lv_power, numeric_display_type

    @staticmethod
    def _parse_error_data(data: bytes) \
            -> ty.Tuple[ErrorCode, ErrorCode, ErrorCode, ErrorCode]:
        if data[0] == 0xa6:
            dust_abnormal_value = ErrorCode(data[1])
            brush_abnormal_value = ErrorCode(data[2])
            dust_full_abnormal_value = ErrorCode(data[3])
            temp_high_abnormal_value = ErrorCode(data[4])
            if brush_abnormal_value == ErrorCode.DustUnInstall:
                brush_abnormal_value = ErrorCode.MotorAbnormal
            return (
                dust_abnormal_value, brush_abnormal_value,
                dust_full_abnormal_value, temp_high_abnormal_value,
            )
        return ErrorCode.OK, ErrorCode.OK, ErrorCode.OK, ErrorCode.OK

    async def _read_state_from_device(self):
        voltage_bytes = await self.client.read_gatt_char(self.VOLTAGE_CHAR)
        voltage, battery = self._parse_voltage_data(voltage_bytes)
        state_bytes = await self.client.read_gatt_char(self.VOLTAGE_CHAR)
        state, speed_level, low_speed_lv_power, numeric_display_type = (
            self._parse_state_data(state_bytes))
        total_use_bytes = await self.client.read_gatt_char(self.TOTAL_USE_CHAR)
        total_use_seconds, total_clean_seconds, hepa_used_seconds = (
            self._parse_usage_data(total_use_bytes))
        error_bytes = await self.client.read_gatt_char(self.ERROR_CHAR)
        (
            dust_abnormal_value, brush_abnormal_value,
            dust_full_abnormal_value, temp_high_abnormal_value,
        ) = self._parse_error_data(error_bytes)

        return CleanerState(
            voltage=voltage,
            battery=battery,
            state=state,
            speed_level=speed_level,
            low_speed_lv_power=low_speed_lv_power,
            numeric_display_type=numeric_display_type,
            total_use_seconds=total_use_seconds,
            total_clean_seconds=total_clean_seconds,
            hepa_used_seconds=hepa_used_seconds,
            dust_abnormal_value=dust_abnormal_value,
            brush_abnormal_value=brush_abnormal_value,
            dust_full_abnormal_value=dust_full_abnormal_value,
            temp_high_abnormal_value=temp_high_abnormal_value,
        )

    def voltage_notification_handler(self, sender, data: bytearray):
        """
        sender.handle == 28
        """
        _LOGGER.debug(
            "voltage_notification_handler: {0} notification: {1}: {2}".format(
                self,
                sender,
                format_binary(data),
            )
        )
        self._state.voltage, self._state.battery = (
            self._parse_voltage_data(data))

    def state_notification_handler(self, sender, data: bytearray):
        """
        sender.handle == 32
        """
        _LOGGER.debug(
            "state_notification_handler: {0} notification: {1}: {2}".format(
                self,
                sender,
                format_binary(data),
            ),
        )
        state, speed_level, low_speed_lv_power, numeric_display_type = (
            self._parse_state_data(data))
        self._state.state = state
        self._state.speed_level = speed_level
        self._state.low_speed_lv_power = low_speed_lv_power
        self._state.numeric_display_type = numeric_display_type

    def total_use_notification_handler(self, sender, data: bytearray):
        _LOGGER.debug(
            "total_use_notification_handler: {0} notification: {1}: {2}".format(
                self,
                sender,
                format_binary(data),
            ),
        )
        total_use_seconds, total_clean_seconds, hepa_used_seconds = (
            self._parse_usage_data(data))
        self._state.total_use_seconds = total_use_seconds
        self._state.total_clean_seconds = total_clean_seconds
        self._state.hepa_used_seconds = hepa_used_seconds

    def error_notification_handler(self, sender, data: bytearray):
        _LOGGER.debug(
            "error_notification_handler: {0} notification: {1}: {2}".format(
                self,
                sender,
                format_binary(data),
            ),
        )
        (
            dust_abnormal_value, brush_abnormal_value,
            dust_full_abnormal_value, temp_high_abnormal_value,
        ) = self._parse_error_data(data)
        self._state.dust_abnormal_value = dust_abnormal_value
        self._state.brush_abnormal_value = brush_abnormal_value
        self._state.dust_full_abnormal_value = dust_full_abnormal_value
        self._state.temp_high_abnormal_value = temp_high_abnormal_value

    async def get_device_data(self):
        # don't call super(), the device doesn't have FIRMWARE_VERSION
        self._model = 'Vacuum cleaner'
        name = await self._read_with_timeout(DEVICE_NAME)
        if isinstance(name, (bytes, bytearray)):
            self._model = name.decode().strip('\0')

        version_bytes = await self._read_with_timeout(VERSION_CHAR)
        self._version = self._parse_version_data(version_bytes)[1]
        _LOGGER.debug(f'{self} name: {self._model}, version: {self._version}')
        self._state = await self._read_state_from_device()

        _LOGGER.debug(f'{self} initial state: {self._state}')
        for ch, handler in (
            (self.VOLTAGE_CHAR, self.voltage_notification_handler),
            (self.STATE_CHAR, self.state_notification_handler),
            (self.TOTAL_USE_CHAR, self.total_use_notification_handler),
            (self.ERROR_CHAR, self.error_notification_handler),
        ):
            await self.client.start_notify(ch, handler)

    async def _set_speed_level(self, speed_level: SpeedLvPower):
        await self.client.write_gatt_char(
            self.STATE_CHAR,
            b'\xa2\x51' + speed_level.value.to_bytes(2, 'big'),
            response=False,
        )

    async def _reset_hepa_filter(self):
        await self.client.write_gatt_char(
            self.STATE_CHAR,
            b'\xa2\x52',
            response=False,
        )

    async def handle_messages(self, publish_topic, *args, **kwargs):
        while True:
            try:
                if not self.client.is_connected:
                    raise ConnectionError()
                message = await aio.wait_for(
                    self.message_queue.get(),
                    timeout=60,
                )
            except aio.TimeoutError:
                await aio.sleep(1)
                continue
            value = message['value']
            entity_topic, action_postfix = self.get_entity_subtopic_from_topic(
                message['topic'],
            )
            entity = self.get_entity_by_name(SELECT_DOMAIN, LOW_SPEED_POWER)
            if entity_topic == self._get_topic_for_entity(
                entity,
                skip_unique_id=True,
            ):
                try:
                    speed_power = SpeedLvPower.from_option(value)
                except KeyError:
                    _LOGGER.warning(f'Unknown speed option: {value}')
                    continue

                _LOGGER.info(
                    f'[{self}] switch {LOW_SPEED_POWER} value={speed_power}',
                )
                while True:
                    try:
                        await self._set_speed_level(speed_power)
                        self._state.low_speed_lv_power = speed_power
                        await aio.gather(
                            publish_topic(
                                topic=self._get_topic_for_entity(entity),
                                value=speed_power.to_option(),
                            ),
                            self._notify_state(publish_topic),
                            **get_loop_param(self._loop),
                        )
                        break
                    except ConnectionError as e:
                        _LOGGER.exception(str(e))
                    await aio.sleep(5)
                continue

            entity = self.get_entity_by_name(BUTTON_DOMAIN, RESET_HEPA)
            if entity_topic == self._get_topic_for_entity(
                entity,
                skip_unique_id=True,
            ):
                _LOGGER.info(f'[{self}] reset HEPA filter')
                while True:
                    try:
                        await self._reset_hepa_filter()
                        self._state.hepa_used_seconds = 0
                        await self._notify_state(publish_topic)
                        break
                    except ConnectionError as e:
                        _LOGGER.exception(str(e))
                    await aio.sleep(5)
                continue

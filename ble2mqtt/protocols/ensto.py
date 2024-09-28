import abc
import datetime
import logging
import struct
import uuid
from dataclasses import dataclass
from enum import Enum

from bleak import BleakError

from ble2mqtt.devices.base import BaseDevice

from ..utils import format_binary
from .base import BLEQueueMixin

_LOGGER = logging.getLogger(__name__)


class EnstoNotAuthorized(Exception):
    pass


class ActiveMode(Enum):
    MANUAL = 1
    CALENDAR = 2
    VACATION = 3


class ActiveHeatingMode(Enum):
    FLOOR = 1
    ROOM = 2
    COMBINATION = 3
    POWER = 4
    FORCE = 5


@dataclass
class Measurements:
    target_temperature: float
    temperature: float
    floor_temperature: float
    room_temperature: float
    relay_is_on: bool
    alarm_code: int
    active_mode: ActiveMode
    active_heating_mode: ActiveHeatingMode
    boost_is_on: bool
    potentiometer: int
    boost_minutes: int = 0
    boost_minutes_left: int = 0


class EnstoProtocol(BLEQueueMixin, BaseDevice, abc.ABC):
    MEASUREMENTS_CHAR: uuid.UUID = None  # type: ignore
    VACATION_CHAR: uuid.UUID = None  # type: ignore
    DATE_CHAR: uuid.UUID = None  # type: ignore
    CUSTOM_MEMORY_SLOT_CHAR: uuid.UUID = None  # type: ignore
    AUTH_CHAR: uuid.UUID = None  # type: ignore

    def __init__(self, *args, **kwargs):
        self._reset_id = None
        super().__init__(*args, **kwargs)
        self._heater_potentiometer_temperature = 20.0  # temp on potentiometer

    async def protocol_start(self):
        await self.auth()
        await self.set_date()

    async def auth(self):
        # Need to check if key is provided. Otherwise, read the reset_ket from
        # characteristic
        if not self._reset_id:
            # TODO: pairing in python code doesn't work.
            #   Use one-time bluetoothctl pairing
            # _LOGGER.info('pairing...')
            # await self.client.pair()
            _LOGGER.info(f'{self} reading RESET_ID_CHAR {self.AUTH_CHAR}')
            try:
                data = await self.client.read_gatt_char(self.AUTH_CHAR)
            except BleakError as e:
                if 'NotAuthorized' in str(e):
                    raise EnstoNotAuthorized(
                        f'{self} {self.AUTH_CHAR} is not readable.'
                        f' Switch the thermostat in pairing mode',
                    ) from None
                raise
            _LOGGER.debug(f'{self} reset id: {format_binary(data)}')
            if len(data) >= 10:
                self._reset_id = data[:4]
                _LOGGER.warning(
                    f'{self} [!] Write key {self._reset_id.hex()} '
                    f'to config file for later connection',
                )
            else:
                _LOGGER.error(
                    f'{self} Key is unknown and device is not in pairing mode',
                )
        try:
            await self.client.write_gatt_char(
                self.AUTH_CHAR, self._reset_id, response=True,
            )
        except BleakError as e:
            if 'NotAuthorized' in str(e):
                raise EnstoNotAuthorized(
                    f'{self} has incorrect key: {self._reset_id.hex()}',
                ) from None
            raise

    @staticmethod
    def _parse_measurements(data: bytearray) -> Measurements:
        # first part of reporting data
        target_temperature = \
            int.from_bytes(data[1:3], byteorder='little') / 10
        room_temperature = \
            int.from_bytes(data[4:6], byteorder='little', signed=True) / 10
        floor_temperature = \
            int.from_bytes(data[6:8], byteorder='little', signed=True) / 10
        relay_is_on = data[8] == 1
        alarm_code = int.from_bytes(data[9:13], byteorder='little')

        # 1 - manual, 2 - calendar, 3 - vacation
        active_mode = ActiveMode(data[13])
        active_heating_mode = ActiveHeatingMode(data[14])
        boost_is_on = data[15] == 1
        boost_minutes = int.from_bytes(data[16:18], byteorder='little')
        boost_minutes_left = int.from_bytes(data[18:20], byteorder='little')
        potentiometer = data[20]

        if active_heating_mode == ActiveHeatingMode.FLOOR:
            temperature = floor_temperature
        elif active_heating_mode == ActiveHeatingMode.ROOM:
            temperature = room_temperature
        elif room_temperature == -0.1 and floor_temperature != -0.1:
            temperature = floor_temperature
        elif room_temperature != -0.1 and floor_temperature == -0.1:
            temperature = room_temperature
        else:
            temperature = room_temperature

        return Measurements(
            target_temperature=target_temperature,
            temperature=temperature,
            floor_temperature=floor_temperature,
            room_temperature=room_temperature,
            relay_is_on=relay_is_on,
            alarm_code=alarm_code,
            boost_is_on=boost_is_on,
            boost_minutes=boost_minutes,
            boost_minutes_left=boost_minutes_left,
            potentiometer=potentiometer,
            active_mode=active_mode,
            active_heating_mode=active_heating_mode
        )

    async def read_measurements(self) -> Measurements:
        # f5 32 00 00 b8 00 00 00 00 00 00 00 00 01 02 00 3c 00 3c
        # 00 00 00 05 00 ff ff ff ff ff ff ff ff ff ff ff ff ff ff

        # d2 34 00 00 d8 00 00 00 00 00 00 00 00 03 02 00 3c 00 3c
        # 00 00 00 05 00 ff ff ff ff ff ff ff ff ff ff ff ff ff ff
        data = await self.client.read_gatt_char(self.MEASUREMENTS_CHAR)
        _LOGGER.debug(f'{self} read_measurements: {format_binary(data)}')
        return self._parse_measurements(data)

    async def set_date(self, tzoffset=3):
        _LOGGER.debug(f'{self} set date to current')
        now = datetime.datetime.utcnow() + datetime.timedelta(hours=tzoffset)
        await self.client.write_gatt_char(
            self.DATE_CHAR,
            struct.pack(
                '<H5B',
                now.year,
                now.month,
                now.day,
                now.hour,
                now.minute,
                now.second,
            ),
        )

    async def set_vacation_mode(self, temperature, enable=True):
        offset_temp = int(
            (temperature - self._heater_potentiometer_temperature) * 100,
        )
        data = struct.pack(
            '<10BhbBB',
            0,  # from year
            1,  # from mon
            1,  # from day
            0,
            0,
            255,  # to year
            12,  # to mon
            31,  # to day
            0,
            0,
            offset_temp,
            0,  # offset percentage
            1 if enable else 0,  # enable
            1 if enable else 0,  # vacation mode
        )
        _LOGGER.debug(f'{self} set vacation mode offset: {offset_temp}, '
                      f'{format_binary(data)}')
        await self.client.write_gatt_char(self.VACATION_CHAR, data)

    async def read_vacation_mode(self) -> bytes:
        # 10 07 01 00 00 10 08 01 00 00 0c fe ec 00 00
        data = await self.client.read_gatt_char(self.VACATION_CHAR)
        return data

    async def read_target_temp(self):
        return int.from_bytes((
            await self.client.read_gatt_char(self.CUSTOM_MEMORY_SLOT_CHAR)
        )[:2], byteorder='little')/10

    async def save_target_temp(self, value: float):
        # to keep it working between app restart we store target temp
        # in an unused characteristic
        await self.client.write_gatt_char(
            self.CUSTOM_MEMORY_SLOT_CHAR,
            int(value*10).to_bytes(2, byteorder='little'),
        )

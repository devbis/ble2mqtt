import abc
import logging
import typing as ty
import uuid
from enum import Enum

from ble2mqtt.devices.base import BaseDevice

from ..utils import format_binary
from .base import BLEQueueMixin

_LOGGER = logging.getLogger(__name__)


class MotorTriggerCommandCodes(Enum):
    ADD = 0x13
    REMOVE = 0x23
    READ = 0x33
    EDIT = 0x43
    CLEAR_ALL = 0x63


class MotorCommandCodes(Enum):
    STOP = 0x00
    STOP_AT_NEXT_STEP = 0x01
    STOP_AT_NEXT_STEP_STATE = 0x03
    STEP_UP = 0x68
    UP = 0x69
    STEP_DOWN = 0x86
    DOWN = 0x96
    LOW_BATTERY = 0xff


class ConfigCommandCodes(Enum):
    MOTOR_SPEED = 1
    MOTOR_DIRECTION = 2
    MOTOR_SPEED_TRIGGER = 3
    PID = 4
    GEO_POSITION = 5
    LOCAL_TIME_OFFSET = 6
    MOTOR_ACCELERATION = 7
    MOTOR_DECELERATION = 8
    MOTOR_USTALL_ACCELERATION = 9
    INCREASE_ENCODER_BY2 = 10
    INCREASE_ENCODER_BY4 = 11
    BOOT_SEQ = 12
    RESET_REASON = 13
    STOP_REASON = 14
    POF_COUNT = 15
    SLIP_LENGTH = 16
    ENC_MAX = 17
    ENC_CUR = 18
    SLIP_INTERVAL = 19
    POSITION_MOVE_TOTAL = 20
    MOTOR_MOVE_TOTAL = 21
    IN_CALIBRATION_MODE = 22
    SUNRISE_SUNSET = 23
    MOTOR_CURRENT = 24
    QUERY = 255


class SomaProtocol(BLEQueueMixin, BaseDevice, abc.ABC):
    POSITION_CHAR: uuid.UUID
    MOTOR_CHAR: uuid.UUID
    SET_POSITION_CHAR: uuid.UUID
    BATTERY_CHAR: uuid.UUID
    CHARGING_CHAR: uuid.UUID
    CONFIG_CHAR: uuid.UUID

    LIGHT_COEFF_TO_LUX = 10

    def notification_callback(self, sender_handle: int, data: bytearray):
        _LOGGER.debug(
            f'{self} notification: {sender_handle}: {format_binary(data)}')
        if sender_handle == 71:
            # CONFIG_CHAR
            if data[0] == ConfigCommandCodes.QUERY.value:
                return super().notification_callback(sender_handle, data)
            if data[0] == ConfigCommandCodes.MOTOR_SPEED.value:
                # values are 0x0, 0x3, 0x69, 0x96
                self._handle_motor_run_state(MotorCommandCodes(data[2]))
        elif sender_handle == 32:
            # POSITION_CHAR
            self._handle_position(self._convert_position(data[0]))
        elif sender_handle == 66:
            # CHARGING_CHAR
            self._handle_charging(**self._parse_charge_response(data))

    @staticmethod
    def _convert_position(value):
        return 100 - value

    @abc.abstractmethod
    def _handle_position(self, value):
        pass

    @abc.abstractmethod
    def _handle_charging(self, *, charging_level, panel_level):
        pass

    @abc.abstractmethod
    def _handle_motor_run_state(self, run_state: MotorCommandCodes):
        pass

    async def _get_position(self):
        response = await self.client.read_gatt_char(self.POSITION_CHAR)
        _LOGGER.debug(f'{self} _get_position: [{format_binary(response)}]')
        return self._convert_position(response[0])

    async def _get_target_position(self):
        response = await self.client.read_gatt_char(self.SET_POSITION_CHAR)
        _LOGGER.debug(
            f'{self} _get_target_position: [{format_binary(response)}]',
        )
        return self._convert_position(response[0])

    async def _get_battery(self):
        response = await self.client.read_gatt_char(self.BATTERY_CHAR)
        _LOGGER.debug(
            f'{self} _get_battery: [{format_binary(response)}]',
        )
        return int(min(100.0, response[0] / 75 * 100))

    async def _get_light_and_panel(self):
        response = await self.client.read_gatt_char(self.CHARGING_CHAR)
        _LOGGER.debug(
            f'{self} _get_light_and_panel: [{format_binary(response)}]',
        )
        return self._parse_charge_response(response)

    async def _set_position(self, value):
        value = self._convert_position(value)
        _LOGGER.debug(f'{self} _set_position: {value}')
        await self.client.write_gatt_char(
            self.SET_POSITION_CHAR,
            bytes([value]),
            response=False,
        )

    def _parse_config_response(self, data):
        result = {}
        if data[0] != ConfigCommandCodes.QUERY.value:
            return result

        offset = 2
        while offset < len(data):
            cmd = data[offset]
            length = data[offset + 1]
            offset += 2
            value = data[offset:offset + length]
            if length == 1:
                value = value[0]
            result[ConfigCommandCodes(cmd)] = value
            offset += length
        return result

    def _parse_charge_response(self, data):
        return {
            'charging_level': (
                int.from_bytes(data[:2], byteorder='little') *
                self.LIGHT_COEFF_TO_LUX
            ),
            'panel_level': int.from_bytes(data[2:4], byteorder='little'),
        }

    async def _get_motor_speed(self) -> ty.Optional[int]:
        cmd = bytes([
            ConfigCommandCodes.QUERY.value,
            0x01,  # length
            ConfigCommandCodes.MOTOR_SPEED.value,
        ])
        self.clear_ble_queue()
        await self.client.write_gatt_char(
            self.CONFIG_CHAR,
            cmd,
            response=True,
        )
        ble_notification = await self.ble_get_notification(timeout=10)
        parsed = self._parse_config_response(ble_notification[1])
        _LOGGER.debug(f'_motor_speed parsed {parsed}')
        return parsed.get(ConfigCommandCodes.MOTOR_SPEED, None)

    async def _set_motor_speed(self, value):
        value = value & 0xff
        _LOGGER.debug(f'{self} _set_motor_speed: {value}')
        cmd = bytes([ConfigCommandCodes.MOTOR_SPEED.value, 0x01, value])
        _LOGGER.debug(f'{self} _set_motor_speed cmd [{format_binary(cmd)}]')
        await self.client.write_gatt_char(
            self.CONFIG_CHAR,
            cmd,
            response=True,
        )

    async def _stop(self):
        resp = await self.client.write_gatt_char(
            self.MOTOR_CHAR,
            bytes([MotorCommandCodes.STOP.value]),
            response=True,
        )
        _LOGGER.debug(f'{self} _stop: {resp}')

    # async def _open(self):
    #     await self.client.write_gatt_char(
    #         self.MOTOR_CHAR,
    #         bytes([MotorCommandCodes.UP.value]),
    #         response=True,
    #     )
    #
    # async def _close(self):
    #     await self.client.write_gatt_char(
    #         self.MOTOR_CHAR,
    #         bytes([MotorCommandCodes.DOWN.value]),
    #         response=False,
    #     )

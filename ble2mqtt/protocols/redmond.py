import abc
import asyncio as aio
import logging
import struct
import time
import uuid
from dataclasses import dataclass
from enum import Enum, IntEnum

from ..devices.base import BaseDevice
from ..utils import format_binary
from .base import BaseCommand, BLEQueueMixin, SendAndWaitReplyMixin

_LOGGER = logging.getLogger(__name__)


BOIL_TIME_RELATIVE_DEFAULT = 0x80


class RedmondError(ValueError):
    pass


class Command(IntEnum):
    VERSION = 0x01
    RUN_CURRENT_MODE = 0x03
    STOP_CURRENT_MODE = 0x04
    WRITE_MODE = 0x05
    READ_MODE = 0x06
    WRITE_TEMPERATURE = 0x0b
    WRITE_DELAY = 0x0c
    WRITE_IONIZATION = 0x1b
    WRITE_COLOR = 0x32
    READ_COLOR = 0x33
    SET_BACKLIGHT_MODE = 0x37
    SET_SOUND = 0x3c
    SET_LOCK = 0x3e
    GET_STATISTICS = 0x47
    GET_STARTS_COUNT = 0x50
    SET_TIME = 0x6e
    AUTH = 0xFF


class KettleG200Mode(IntEnum):
    BOIL = 0x00
    HEAT = 0x01
    LIGHT = 0x03
    UNKNOWN1 = 0x04
    UNKNOWN2 = 0x05
    UNKNOWN3 = 0x06


class CookerAfterCookMode(IntEnum):
    HEAT_AFTER_COOK = 0x00
    OFF_AFTER_COOK = 0x01


class KettleRunState(IntEnum):
    OFF = 0x00
    SETUP_PROGRAM = 0x01  # for cooker
    ON = 0x02  # cooker - delayed start
    HEAT = 0x03  # for cooker
    COOKING = 0x05  # for cooker
    WARM_UP = 0x06  # for cooker


class CookerRunState(IntEnum):
    OFF = 0x00
    SETUP_PROGRAM = 0x01  # for cooker
    DELAYED_START = 0x02  # cooker - delayed start
    HEAT = 0x03  # for cooker
    COOKING = 0x05  # for cooker
    WARM_UP = 0x06  # for cooker


class ColorTarget(Enum):
    BOIL = 0x00
    LIGHT = 0x01


class CookerM200Program(Enum):
    FRYING = 0x0
    RICE = 0x1
    MANUAL = 0x2
    PILAF = 0x3
    STEAM = 0x4
    BAKING = 0x5
    STEWING = 0x6
    SOUP = 0x7
    PORRIDGE = 0x8
    YOGHURT = 0x9
    EXPRESS = 0xa


class CookerSubProgram(Enum):
    NONE = 0
    VEGETABLES = 1
    FISH = 2
    MEAT = 3


@dataclass
class KettleG200State:
    temperature: int = 0
    color_change_period: int = 0xf
    mode: KettleG200Mode = KettleG200Mode.BOIL
    target_temperature: int = 0
    sound: bool = True
    is_blocked: bool = False
    state: KettleRunState = KettleRunState.OFF
    boil_time: int = 0
    error: int = 0

    FORMAT = '<6BH2BH4B'

    @classmethod
    def from_bytes(cls, response):
        # 00 00 00 00 01 16 0f 00 00 00 00 00 00 80 00 00 - wait
        # 00 00 00 00 01 14 0f 00 02 00 00 00 00 80 00 00 - boil
        # 01 00 28 00 01 19 0f 00 00 00 00 00 00 80 00 00 - 40º keep
        (
            mode,  # 0
            submode,  # 1
            target_temp,  # 2
            is_blocked,  # 3
            sound,  # 4
            current_temp,  # 5
            color_change_period,  # 6-7
            state,  # 8
            _,  # 9
            ionization,  # 10,11  # for air purifier
            _,   # 12,
            boil_time_relative,  # 13
            _,  # 14
            error,  # 15
        ) = struct.unpack(cls.FORMAT, response)
        return cls(
            mode=KettleG200Mode(mode),
            target_temperature=target_temp,
            sound=sound,
            temperature=current_temp,
            state=KettleRunState(state),
            boil_time=boil_time_relative - BOIL_TIME_RELATIVE_DEFAULT,
            color_change_period=color_change_period,
            error=error,
        )

    def to_bytes(self):
        return struct.pack(
            self.FORMAT,
            self.mode.value,
            0,
            self.target_temperature,
            1 if self.is_blocked else 0,
            self.sound,
            self.temperature,
            self.color_change_period,
            self.state.value,
            0,
            0,
            0,
            self.boil_time + BOIL_TIME_RELATIVE_DEFAULT,
            0,
            0,  # don't send error
        )


@dataclass
class CookerState:
    program: CookerM200Program = CookerM200Program.RICE
    subprogram: CookerSubProgram = CookerSubProgram.NONE
    target_temperature: int = 0
    program_minutes: int = 0
    timer_minutes: int = 0
    after_cooking_mode: CookerAfterCookMode = \
        CookerAfterCookMode.HEAT_AFTER_COOK
    state: CookerRunState = CookerRunState.OFF
    sound: bool = True
    locked: bool = False

    SET_FORMAT = '<8B'
    FORMAT = f'{SET_FORMAT}6BH'

    @classmethod
    def from_bytes(cls, response):
        # 00 00 00 00 00 00 00 00 00 00 01 00 00 00 00 00 - wait with sound
        # 00 00 64 00 00 00 00 00 00 01 01 00 00 00 00 00 - locked
        # 00 00 96 00 0f 00 0f 01 00 00 00 00 00 00 00 00 - 150º 15 minutes

        (
            program,  # 0
            subprogram,  # 1
            target_temp,  # 2
            program_hours,  # 3
            program_minutes,  # 4
            timer_hours,  # 5
            timer_minutes,  # 6
            after_cooking_mode,  # 7
            state,  # 8
            locked,  # 9
            sound,  # 10
            _,  # 11
            _,   # 12,
            _,  # 13
            error,  # 14,15
        ) = struct.unpack(cls.FORMAT, response)
        return cls(
            program=CookerM200Program(program),
            subprogram=CookerSubProgram(subprogram),
            target_temperature=target_temp,
            after_cooking_mode=CookerAfterCookMode(after_cooking_mode),
            state=CookerRunState(state),
            program_minutes=(program_minutes + program_hours * 60),
            timer_minutes=(timer_minutes + timer_hours * 60),
            sound=bool(sound),
            locked=bool(locked),
        )

    def to_bytes(self):
        return struct.pack(
            self.SET_FORMAT,
            self.program.value,
            self.subprogram.value,
            self.target_temperature,
            self.program_minutes // 60,
            self.program_minutes % 60,
            self.timer_minutes // 60,
            self.timer_minutes % 60,
            self.after_cooking_mode.value,
        )


_COOKER_M200_PREDEFINED_PROGRAMS_VALUES = [
    # program, subprogram, temperature, hours, minutes, dhours, dminutes, heat
    [0x00, 0x00, 0x96, 0x00, 0x0f, 0x00, 0x00, 0x01],
    [0x01, 0x00, 0x64, 0x00, 0x19, 0x00, 0x00, 0x01],
    [0x02, 0x00, 0x64, 0x00, 0x1e, 0x00, 0x00, 0x01],
    [0x03, 0x00, 0x6e, 0x01, 0x00, 0x00, 0x00, 0x01],
    [0x04, 0x00, 0x64, 0x00, 0x19, 0x00, 0x00, 0x01],
    [0x05, 0x00, 0x8c, 0x01, 0x00, 0x00, 0x00, 0x01],
    [0x06, 0x00, 0x64, 0x01, 0x00, 0x00, 0x00, 0x01],
    [0x07, 0x00, 0x64, 0x01, 0x00, 0x00, 0x00, 0x01],
    [0x08, 0x00, 0x64, 0x00, 0x1e, 0x00, 0x00, 0x01],
    [0x09, 0x00, 0x28, 0x08, 0x00, 0x00, 0x00, 0x00],
    [0x0a, 0x00, 0x64, 0x00, 0x1e, 0x00, 0x00, 0x00],
]


COOKER_PREDEFINED_PROGRAMS = {
    CookerM200Program(v[0]).name.lower(): CookerState(
        program=CookerM200Program(v[0]),
        subprogram=CookerSubProgram(v[1]),
        target_temperature=v[2],
        program_minutes=v[3] * 60 + v[4],
        timer_minutes=v[5] * 60 + v[6],
        after_cooking_mode=CookerAfterCookMode(v[7]),
    )
    for v in _COOKER_M200_PREDEFINED_PROGRAMS_VALUES
}


class RedmondCommand(BaseCommand):
    def __init__(self, cmd, payload, *args, **kwargs):
        super().__init__(cmd, *args, **kwargs)
        self.payload = payload


class RedmondBaseProtocol(SendAndWaitReplyMixin, BLEQueueMixin, BaseDevice,
                          abc.ABC):
    MAGIC_START = 0x55
    MAGIC_END = 0xaa

    RX_CHAR: uuid.UUID = None  # type: ignore
    TX_CHAR: uuid.UUID = None  # type: ignore

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cmd_counter = 0

    def _get_command(self, cmd: int, payload: bytes):
        container = struct.pack(
            '<4B',
            self.MAGIC_START,
            self._cmd_counter,
            cmd,
            self.MAGIC_END,
        )
        self._cmd_counter += 1
        if self._cmd_counter > 100:
            self._cmd_counter = 0
        return bytearray(b'%b%b%b' % (container[:3], payload, container[3:]))

    async def send_command(self, cmd: Command, payload: bytes = b'',
                           wait_reply=True, timeout=25):
        command = RedmondCommand(
            cmd,
            payload=payload,
            wait_reply=wait_reply,
            timeout=timeout,
        )
        await self.cmd_queue.put(command)
        return await aio.wait_for(command.answer, timeout)

    async def process_command(self, command: RedmondCommand):
        cmd = self._get_command(command.cmd.value, command.payload)
        _LOGGER.debug(
            f'... send cmd {command.cmd.value:04x} ['
            f'{format_binary(command.payload, delimiter="")}] '
            f'{format_binary(cmd)}',
        )
        self.clear_ble_queue()
        cmd_resp = await aio.wait_for(
            self.client.write_gatt_char(self.TX_CHAR, cmd, True),
            timeout=command.timeout,
        )
        if not command.wait_reply:
            if command.answer.cancelled():
                return
            command.answer.set_result(cmd_resp)
            return

        ble_notification = await self.ble_get_notification(command.timeout)

        # extract payload from container
        cmd_resp = bytes(ble_notification[1][3:-1])
        if command.answer.cancelled():
            return
        command.answer.set_result(cmd_resp)

    async def protocol_start(self):
        # we assume that every time protocol starts it uses new blank
        # BleakClient to avoid multiple char notifications on every restart
        # bug ?

        assert self.RX_CHAR and self.TX_CHAR
        # if not self.notification_started:
        assert self.client.is_connected
        assert not self._cmd_queue_task.done()
        # check for fresh client
        assert not self.client._notification_callbacks
        _LOGGER.debug(f'Enable BLE notifications from [{self.client.address}]')
        await self.client.write_gatt_char(
            self.TX_CHAR,
            bytearray(0x01.to_bytes(2, byteorder="little")),
            True,
        )
        await self.client.start_notify(
            self.RX_CHAR,
            self.notification_callback,
        )

    async def protocol_stop(self):
        # NB: not used for now as we don't disconnect from our side
        await self.client.stop_notify(self.RX_CHAR)

    async def close(self):
        self.clear_cmd_queue()
        await super().close()

    @staticmethod
    def _check_success(response,
                       error_msg="Command was not completed successfully"):
        success = response and response[0]
        if not success:
            raise RedmondError(error_msg)

    @staticmethod
    def _check_zero_response(response,
                             error_msg="Command was not completed successfully",
                             ):
        response = response and response[0]
        if response != 0:
            raise RedmondError(error_msg)


class RedmondCommonProtocol(RedmondBaseProtocol, abc.ABC):
    """ Shared methods between different devices """
    async def login(self, key):
        _LOGGER.debug('logging in...')
        resp = await self.send_command(Command.AUTH, key, True)
        self._check_success(resp, "Not logged in")

    async def get_version(self):
        _LOGGER.debug('fetching version...')
        resp = await self.send_command(Command.VERSION, b'', True)
        version = tuple(resp)
        _LOGGER.debug(f'version: {version}')
        return version

    async def run(self):
        _LOGGER.debug('Run mode')
        resp = await self.send_command(Command.RUN_CURRENT_MODE)
        self._check_success(resp)

    async def stop(self):
        _LOGGER.debug('Stop mode')
        resp = await self.send_command(Command.STOP_CURRENT_MODE)
        self._check_success(resp)


class RedmondKettle200Protocol(RedmondCommonProtocol, abc.ABC):
    async def set_time(self, ts=None):
        if ts is None:
            ts = time.time()
        ts = int(ts)
        offset = time.timezone \
            if (time.localtime().tm_isdst == 0) else time.altzone
        _LOGGER.debug(f'Setting time ts={ts} offset={offset}')
        resp = await self.send_command(
            Command.SET_TIME,
            struct.pack('<ii', ts, -offset * 60 * 60),
        )
        self._check_zero_response(resp, 'Cannot set time')

    async def get_mode(self):
        _LOGGER.debug('Get mode...')
        response = await self.send_command(Command.READ_MODE)
        return KettleG200State.from_bytes(response)

    async def set_mode(self, state: KettleG200State):
        _LOGGER.debug('Set mode...')
        resp = await self.send_command(
            Command.WRITE_MODE,
            state.to_bytes(),
        )
        success = resp[0]
        if not success:
            raise RedmondError('Cannot set mode')

    async def set_color(self, mode: ColorTarget, r, g, b, brightness):
        # scale_light = [0x00, 0x32, 0x64]
        scale_light = [0x64, 0x64, 0x64]

        rgb_start = rgb_mid = rgb_end = \
            (b << 24) + (g << 16) + (r << 8) + brightness

        resp = await self.send_command(
            Command.WRITE_COLOR,
            struct.pack(
                '<BBIBIBI',
                mode.value,
                scale_light[0],
                rgb_start,
                scale_light[1],
                rgb_mid,
                scale_light[2],
                rgb_end,
            ),
        )
        self._check_zero_response(resp, 'Cannot set color')

    async def get_statistics(self):
        _LOGGER.debug('Get statistics')
        # b'\x00\x00\xdf\x01\x00\x00$\x01\x00\x00\t\x00\x00\x00\x00\x00'
        resp = await self.send_command(Command.GET_STATISTICS, b'\0')
        _, seconds_run, watts_hours, starts, _, _ = \
            struct.unpack('<HIIHHH', resp)
        return {
            'watts_hours': watts_hours,
            'seconds_run': seconds_run,
            'starts': starts,
        }

    async def get_starts_count(self):
        _LOGGER.debug('Get number of start')
        # b'\x00\x00\x00\t\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
        # b'\x00\x00\x00\n\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
        resp = await self.send_command(Command.GET_STARTS_COUNT, b'\0')
        _, _, starts, *_ = struct.unpack('<BHHHHHHHB', resp)
        return starts


class RedmondCookerProtocol(RedmondCommonProtocol, abc.ABC):
    async def get_mode(self):
        _LOGGER.debug('Get mode...')
        response = await self.send_command(Command.READ_MODE)
        return CookerState.from_bytes(response)

    async def set_mode(self, state: CookerState, ignore_result=True):
        _LOGGER.debug(f'Set mode {state}...')
        resp = await self.send_command(
            Command.WRITE_MODE,
            state.to_bytes(),
        )
        success = resp[0]
        if not ignore_result and not success:
            raise RedmondError('Cannot set mode')

    async def set_predefined_program(self, mode_name: str):
        _LOGGER.debug(f'Set predefined mode {mode_name}...')
        await self.set_mode(COOKER_PREDEFINED_PROGRAMS[mode_name])

    async def set_delay(self, minutes: int):
        _LOGGER.debug('Set delay...')
        resp = await self.send_command(
            Command.WRITE_DELAY,
            bytes([
                minutes // 60,
                minutes % 60,
            ]),
        )
        success = resp[0]
        if not success:
            raise RedmondError('Cannot set delay')

    async def set_temperature(self, temperature: int):
        _LOGGER.debug('Set temperature...')
        resp = await self.send_command(
            Command.WRITE_TEMPERATURE,
            bytes([temperature]),
        )
        success = resp[0]
        if not success:
            raise RedmondError('Cannot set temperature')

    async def set_lock(self, value: bool):
        _LOGGER.debug(f'Set lock {value}...')
        resp = await self.send_command(
            Command.SET_LOCK,
            bytes([1 if value else 0]),
        )
        success = resp[0]
        if not success:
            raise RedmondError('Cannot set lock')

    async def set_sound(self, value: bool):
        _LOGGER.debug(f'Set sound {value}...')
        resp = await self.send_command(
            Command.SET_SOUND,
            bytes([1 if value else 0]),
        )
        success = resp[0]
        if not success:
            raise RedmondError('Cannot set sound')

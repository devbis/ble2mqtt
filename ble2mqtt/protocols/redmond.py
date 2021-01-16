import asyncio as aio
import logging
import struct
import time
from dataclasses import dataclass
from enum import Enum

from ..devices.base import BaseDevice

logger = logging.getLogger(__name__)


BOIL_TIME_RELATIVE_DEFAULT = 0x80


class RedmondError(ValueError):
    pass


class Command(Enum):
    VERSION = 0x01
    RUN_CURRENT_MODE = 0x03
    STOP_CURRENT_MODE = 0x04
    WRITE_MODE = 0x05
    READ_MODE = 0x06
    SET_TIME = 0x6e
    WRITE_COLOR = 0x32
    READ_COLOR = 0x33
    SET_BACKLIGHT_MODE = 0x37
    GET_STATISTICS = 0x47
    GET_STARTS_COUNT = 0x50
    AUTH = 0xFF


class Mode(Enum):
    BOIL = 0x00
    HEAT = 0x01
    LIGHT = 0x03


class RunState(Enum):
    OFF = 0x00
    ON = 0x02


class ColorTarget(Enum):
    BOIL = 0x00
    LIGHT = 0x01


@dataclass
class Kettle200State:
    temperature: int = 0
    color_change_period: int = 0xf
    mode: Mode = Mode.BOIL
    target_temperature: int = 0
    sound: bool = True
    state: RunState = RunState.OFF
    boil_time: int = 0
    error: int = 0

    FORMAT = '<HH2B3H2BH'

    @classmethod
    def from_bytes(cls, response):
        # 00 00 00 00 01 16 0f 00 00 00 00 00 00 80 00 00 - wait
        # 00 00 00 00 01 14 0f 00 02 00 00 00 00 80 00 00 - boil
        # 01 00 28 00 01 19 0f 00 00 00 00 00 00 80 00 00 - 40º keep
        (
            mode,  # 0,1
            target_temp,  # 2,3
            sound,  # 4
            current_temp,  # 5
            color_change_period,  # 6-7
            state,  # 8,9
            _,  # 10,11
            _,   # 12,
            boil_time_relative,  # 13
            error,  # 14,15
        ) = struct.unpack(cls.FORMAT, response)
        return cls(
            mode=Mode(mode),
            target_temperature=target_temp,
            sound=sound,
            temperature=current_temp,
            state=RunState(state),
            boil_time=boil_time_relative - BOIL_TIME_RELATIVE_DEFAULT,
            color_change_period=color_change_period,
            error=error,
        )

    def to_bytes(self):
        return struct.pack(
            self.FORMAT,
            self.mode.value,
            self.target_temperature,
            self.sound,
            self.temperature,
            self.color_change_period,
            self.state.value,
            0,
            0,
            self.boil_time + BOIL_TIME_RELATIVE_DEFAULT,
            self.error,
        )


class KettleCommand:
    def __init__(self, cmd, payload, wait_reply, timeout):
        self.cmd = cmd
        self.payload = payload
        self.wait_reply = wait_reply
        self.timeout = timeout
        self.answer = aio.Future()


class RedmondKettle200Protocol(BaseDevice):
    MAGIC_START = 0x55
    MAGIC_END = 0xaa

    RX_CHAR = None
    TX_CHAR = None

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._cmd_counter = 0
        self.wait_event = aio.Event()
        self.received_data = None
        self.cmd_queue = aio.Queue()
        self.queue_handler = None

    def notification_handler(self, sender: int, data: bytearray):
        logger.debug("Notification: {0}: {1}".format(
            sender,
            ' '.join(format(x, '02x') for x in data),
        ))
        if not self.wait_event.is_set():
            self.received_data = data
            self.wait_event.set()

    async def handle_queue(self):
        while True:
            command: KettleCommand = await self.cmd_queue.get()
            cmd = self._get_command(command.cmd.value, command.payload)
            logger.debug(
                f'... send cmd {command.cmd.value:04x} ['
                f'{"".join(format(x, "02x") for x in command.payload)}] '
                f'{" ".join(format(x, "02x") for x in cmd)}',
            )
            self.wait_event.clear()
            cmd_resp = await self.client.write_gatt_char(
                self.TX_CHAR,
                cmd,
                True,
            )
            if not command.wait_reply:
                command.answer.set_result(cmd_resp)

            await aio.wait_for(
                self.wait_event.wait(),
                timeout=command.timeout,
            )

            # extract payload from container
            cmd_resp = bytes(self.received_data[3:-1])
            self.wait_event.clear()
            self.received_data = None
            command.answer.set_result(cmd_resp)

    async def protocol_start(self):
        # we assume that every time protocol starts it uses new blank
        # BleakClient to avoid multiple char notifications on every restart
        # bug ?

        assert self.RX_CHAR and self.TX_CHAR
        # if not self.notification_started:
        assert self.client.is_connected
        # check for fresh client
        assert not self.client._notification_callbacks
        logger.debug(f'Enable BLE notifications from [{self.client.address}]')
        await self.client.write_gatt_char(
            self.TX_CHAR,
            bytearray(0x01.to_bytes(2, byteorder="little")),
            True,
        )
        await self.client.start_notify(
            self.RX_CHAR,
            self.notification_handler,
        )
        self.queue_handler = aio.create_task(self.handle_queue())
        return self.queue_handler

    async def protocol_stop(self):
        # NB: not used for now as we don't disconnect from our side
        self.queue_handler.cancel()
        await self.client.stop_notify(self.RX_CHAR)

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
        cmd = KettleCommand(cmd, payload, wait_reply, timeout)
        await self.cmd_queue.put(cmd)
        return await cmd.answer

    async def login(self, key):
        logger.debug('logging in...')
        resp = await self.send_command(Command.AUTH, key, True)
        self._check_success(resp, "Not logged in")

    async def get_version(self):
        logger.debug('fetching version...')
        resp = await self.send_command(Command.VERSION, b'', True)
        version = tuple(resp)
        logger.debug(f'version: {version}')
        return version

    async def set_time(self, ts=None):
        if ts is None:
            ts = time.time()
        ts = int(ts)
        offset = time.timezone \
            if (time.localtime().tm_isdst == 0) else time.altzone
        logger.debug(f'Setting time ts={ts} offset={offset}')
        resp = await self.send_command(
            Command.SET_TIME,
            struct.pack('<ii', ts, -offset * 60 * 60),
        )
        self._check_zero_response(resp, 'Cannot set time')

    async def get_mode(self):
        logger.debug('Get mode...')
        response = await self.send_command(Command.READ_MODE)
        return Kettle200State.from_bytes(response)

    async def set_mode(self, state: Kettle200State):
        logger.debug('Set mode...')
        resp = await self.send_command(
            Command.WRITE_MODE,
            state.to_bytes(),
        )
        success = resp[0]
        if not success:
            raise RedmondError('Cannot set mode')

    async def run(self):
        logger.debug('Run mode')
        resp = await self.send_command(Command.RUN_CURRENT_MODE)
        self._check_success(resp)

    async def stop(self):
        logger.debug('Stop mode')
        resp = await self.send_command(Command.STOP_CURRENT_MODE)
        self._check_success(resp)

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
        logger.debug('Get statistics')
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
        logger.debug('Get number of start')
        # b'\x00\x00\x00\t\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
        # b'\x00\x00\x00\n\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'
        resp = await self.send_command(Command.GET_STARTS_COUNT, b'\0')
        _, _, starts, *_ = struct.unpack('<BHHHHHHHB', resp)
        return starts

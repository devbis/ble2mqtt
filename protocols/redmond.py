import asyncio as aio
import logging
import struct
import time
import typing as ty
from dataclasses import dataclass
from enum import Enum

from bleak import BleakClient

logger = logging.getLogger(__name__)


BOIL_TIME_RELATIVE_DEFAULT = 0x80


class Command(Enum):
    VERSION = 0x01
    RUN_CURRENT_MODE = 0x03
    STOP_CURRENT_MODE = 0x04
    WRITE_MODE = 0x05
    READ_MODE = 0x06
    SET_TIME = 0x6e
    GET_STATISTICS = 0x47
    GET_STARTS = 0x50
    AUTH = 0xFF


class Mode(Enum):
    BOIL = 0x00
    HEAT = 0x01
    LIGHT = 0x03


class RunState(Enum):
    OFF = 0x00
    ON = 0x02


@dataclass
class Kettle200State:
    temperature: int = 0
    color_change_period: int = 0xf
    mode: Mode = Mode.BOIL
    max_temperature: int = 0
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
            max_temp,  # 2,3
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
            max_temperature=max_temp,
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
            self.max_temperature,
            self.sound,
            self.temperature,
            self.color_change_period,
            self.state.value,
            0,
            0,
            self.boil_time + BOIL_TIME_RELATIVE_DEFAULT,
            self.error,
        )


class RedmondKettle200Protocol:
    MAGIC_START = 0x55
    MAGIC_END = 0xaa

    RX_CHAR = None
    TX_CHAR = None

    def __init__(self) -> None:
        super().__init__()
        self._cmd_counter = 0
        self.wait_event: ty.Optional[aio.Event] = None
        self.received_data = None
        self.client = None

    def protocol_init(self, client: BleakClient):
        self.client = client

    def notification_handler(self, sender, data):
        logger.debug("Notification: {0:04x}: {1}".format(
            sender,
            ' '.join(format(x, '02x') for x in data),
        ))
        if self.wait_event:
            self.received_data = data
            self.wait_event.set()

    async def start(self):
        assert self.RX_CHAR and self.TX_CHAR
        logger.debug('Connecting')
        await self.client.connect()
        await self.client.is_connected()
        logger.info('Start notification...')
        await self.client.start_notify(self.RX_CHAR, self.notification_handler)
        # send this to receive responses
        await self.client.write_gatt_char(self.TX_CHAR, bytearray([1, 0]), True)

    async def close(self):
        await self.client.stop_notify(self.RX_CHAR)
        await self.client.disconnect()

    @staticmethod
    def _check_success(response,
                       error_msg="Command was not completed successfully"):
        success = response[0]
        if not success:
            raise ValueError(error_msg)

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
                           wait_reply=True):
        command = self._get_command(cmd.value, payload)
        logger.debug(
            f'... send cmd {cmd.value:04x} ['
            f'{"".join(format(x, "02x") for x in payload)} ] '
            f'{" ".join(format(x, "02x") for x in command)}',
        )
        self.wait_event = aio.Event()
        cmd_resp = await self.client.write_gatt_char(
            self.TX_CHAR,
            command,
            True,
        )
        if wait_reply:
            await self.wait_event.wait()
            # extract payload from container
            cmd_resp = bytes(self.received_data[3:-1])
            self.wait_event = None
            self.received_data = None
        return cmd_resp

    async def login(self, key):
        logger.debug('logging in...')
        resp = await self.send_command(Command.AUTH, key, True)
        self._check_success(resp, "Not logged in")

    async def get_version(self):
        logger.debug('fetching version...')
        resp = await self.send_command(Command.VERSION, b'', True)
        version = tuple(resp)
        logger.info(f'version: {version=}')
        return version

    async def set_time(self, ts=None):
        if ts is None:
            ts = time.time()
        ts = int(ts)
        offset = time.timezone \
            if (time.localtime().tm_isdst == 0) else time.altzone
        logger.debug(f'Setting time {ts=} {offset=}')
        resp = await self.send_command(
            Command.SET_TIME,
            struct.pack('<ii', ts, -offset * 60 * 60),
        )
        assert resp == b'\x00'

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
            raise ValueError("Cannot set mode")

    async def run(self):
        logger.debug('Run mode')
        resp = await self.send_command(Command.RUN_CURRENT_MODE)
        self._check_success(resp)

    async def stop(self):
        logger.debug('Stop mode')
        resp = await self.send_command(Command.STOP_CURRENT_MODE)
        self._check_success(resp)

import abc
import asyncio as aio
import logging

from ..devices.base import BaseDevice
from ..utils import format_binary
from .base import BLEQueueMixin

logger = logging.getLogger(__name__)

AM43_DEFAULT_PIN = 8888
# command IDs
AM43_CMD_MOVE = 0x0a
AM43_CMD_LOGIN = 0x17
AM43_CMD_GET_BATTERY = 0xa2
AM43_CMD_GET_ILLUMINANCE = 0xaa
AM43_CMD_GET_POSITION = 0xa7
AM43_CMD_SET_POSITION = 0x0d
AM43_NOTIFY_POSITION = 0xa1

AM43_RESPONSE_ACK = 0x5a
AM43_RESPONSE_NACK = 0xa5
# https://github.com/cpmeister/openhab-addons/commit/f56e6f9793be09a731dfe05dedebf8d60d00e59e#diff-057b414e114be8976f6258b888e463a249753cde37531bc06ec490abb77723ecR354
AM43_REPLY_TIMERS = 0xa8  # get_timers
# https://github.com/cpmeister/openhab-addons/commit/f56e6f9793be09a731dfe05dedebf8d60d00e59e#diff-057b414e114be8976f6258b888e463a249753cde37531bc06ec490abb77723ecR383
AM43_REPLY_SEASON = 0xa9  # get_season


class AM43Protocol(BLEQueueMixin, BaseDevice, abc.ABC):
    DATA_CHAR = None

    def notification_callback(self, sender_handle: int, data: bytearray):
        self.process_data(data)
        self._ble_queue.put_nowait((sender_handle, data))

    @staticmethod
    def _convert_position(value):
        return 100 - value

    convert_to_device = _convert_position
    convert_from_device = _convert_position

    async def send_command(self, cmd_id, data: list,
                           wait_reply=True, timeout=25):
        logger.debug(f'[{self}] - send command 0x{cmd_id:x} {data}')
        cmd = bytearray([0x9a, cmd_id, len(data)] + data)
        csum = 0
        for x in cmd:
            csum = csum ^ x
        cmd += bytearray([csum])

        self.clear_ble_queue()
        await self.client.write_gatt_char(self.DATA_CHAR, cmd)
        ret = None
        if wait_reply:
            logger.debug(f'[{self}] waiting for reply')
            ble_notification = await aio.wait_for(
                self.ble_get_notification(),
                timeout=timeout,
            )
            logger.debug(f'[{self}] reply: {ble_notification[1]}')
            ret = bytes(ble_notification[1])
        return ret

    async def login(self, pin):
        return await self.send_command(
            AM43_CMD_LOGIN,
            list(int(pin).to_bytes(2, byteorder='big')),
        )

    async def _get_position(self):
        await self.send_command(AM43_CMD_GET_POSITION, [0x01], True)

    async def _get_battery(self):
        await self.send_command(AM43_CMD_GET_BATTERY, [0x01], True)

    async def _get_illuminance(self):
        await self.send_command(AM43_CMD_GET_ILLUMINANCE, [0x01], True)

    async def _set_position(self, value):
        await self.send_command(
            AM43_CMD_SET_POSITION,
            [self.convert_to_device(int(value))],
            True,
        )

    async def _stop(self):
        await self.send_command(AM43_CMD_MOVE, [0xcc])

    async def _open(self):
        # not used
        await self.send_command(AM43_CMD_MOVE, [0xdd])

    async def _close(self):
        # not used
        await self.send_command(AM43_CMD_MOVE, [0xee])

    async def _get_full_state(self):
        await self._get_position()
        await self._get_battery()
        await self._get_illuminance()

    @abc.abstractmethod
    def handle_login(self, value):
        pass

    @abc.abstractmethod
    def handle_battery(self, value):
        pass

    @abc.abstractmethod
    def handle_position(self, value):
        pass

    @abc.abstractmethod
    def handle_illuminance(self, value):
        pass

    def process_data(self, data: bytearray):
        if data[1] == AM43_CMD_LOGIN:
            self.handle_login(data[3] == AM43_RESPONSE_ACK)
        elif data[1] == AM43_CMD_GET_BATTERY:
            # b'\x9a\xa2\x05\x00\x00\x00\x00Ql'
            self.handle_battery(int(data[7]))
        elif data[1] == AM43_NOTIFY_POSITION:
            self.handle_position(self.convert_from_device(int(data[4])))
        elif data[1] == AM43_CMD_GET_POSITION:
            # [9a a7 07 0e 32 00 00 00 00 30 36]
            # Bytes in this packet are:
            #  3: Configuration flags, bits are:
            #    1: direction (0 - Reverse, 1 - Forward)
            #    2: operation mode (0 - continuous, 1 - inching)
            #    3: top limit set
            #    4: bottom limit set
            #    5: has light sensor
            #  4: Speed setting
            #  5: Current position
            #  6,7: Shade length.
            #  8: Roller diameter.
            #  9: Roller type.

            self.handle_position(self.convert_from_device(int(data[5])))
        elif data[1] == AM43_CMD_GET_ILLUMINANCE:
            # b'\x9a\xaa\x02\x00\x002'
            self.handle_illuminance(int(data[4]) * 12.5)
        elif data[1] in [AM43_CMD_MOVE, AM43_CMD_SET_POSITION]:
            if data[3] != AM43_RESPONSE_ACK:
                logger.error(f'[{self}] Problem with moving: NACK')
        elif data[1] in [AM43_REPLY_TIMERS, AM43_REPLY_SEASON]:
            # [9a a8 00 32]
            #        ^______ Number of timers
            # [9a a9 10 00 00 00 11 00 00 00 00 01 00 00 11 00 00 00 00 22]

            pass
        else:
            logger.error(
                f'{self} BLE notification unknown response '
                f'[{format_binary(data)}]',
            )

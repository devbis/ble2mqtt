import abc
import logging
import uuid

from ..devices.base import BaseDevice
from ..utils import format_binary
from .base import BLEQueueMixin

_LOGGER = logging.getLogger(__name__)

# command IDs
AM43_CMD_MOVE = 0x0a
AM43_CMD_GET_BATTERY = 0xa2
AM43_CMD_GET_ILLUMINANCE = 0xaa
AM43_CMD_GET_POSITION = 0xa7
AM43_CMD_SET_POSITION = 0x0d
AM43_NOTIFY_POSITION = 0xa1

AM43_RESPONSE_ACK = 0x5a
AM43_RESPONSE_NACK = 0xa5
# 9a a8 0a 01 00 7f 09 1e 01 64 7f 11 00 5a
AM43_REPLY_UNKNOWN1 = 0xa8
# 9a a9 10 00 00 00 11 00 00 00 00 01 00 00 11 00 00 00 00 22
AM43_REPLY_UNKNOWN2 = 0xa9


class AM43Protocol(BLEQueueMixin, BaseDevice, abc.ABC):
    DATA_CHAR: uuid.UUID = None  # type: ignore

    def notification_callback(self, sender_handle: int, data: bytearray):
        self.process_data(data)
        super().notification_callback(sender_handle, data)

    @staticmethod
    def _convert_position(value):
        return 100 - value

    @classmethod
    def convert_to_device(cls, value):
        return cls._convert_position(value)

    @classmethod
    def convert_from_device(cls, value):
        return cls._convert_position(value)

    async def send_command(self, cmd_id, data: list,
                           wait_reply=True, timeout=25):
        _LOGGER.debug(f'[{self}] - send command 0x{cmd_id:x} {data}')
        cmd = bytearray([0x9a, cmd_id, len(data)] + data)
        csum = 0
        for x in cmd:
            csum = csum ^ x
        cmd += bytearray([csum])

        self.clear_ble_queue()
        await self.client.write_gatt_char(self.DATA_CHAR, cmd)
        ret = None
        if wait_reply:
            _LOGGER.debug(f'[{self}] waiting for reply')
            ble_notification = await self.ble_get_notification(timeout)
            _LOGGER.debug(f'[{self}] reply: {repr(ble_notification[1])}')
            ret = bytes(ble_notification[1])
        return ret

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
    def handle_battery(self, value):
        pass

    @abc.abstractmethod
    def handle_position(self, value):
        pass

    @abc.abstractmethod
    def handle_illuminance(self, value):
        pass

    def process_data(self, data: bytearray):
        if data[1] == AM43_CMD_GET_BATTERY:
            # b'\x9a\xa2\x05\x00\x00\x00\x00Ql'
            self.handle_battery(int(data[7]))
        elif data[1] == AM43_NOTIFY_POSITION:
            self.handle_position(self.convert_from_device(int(data[4])))
        elif data[1] == AM43_CMD_GET_POSITION:
            # [9a a7 07 0e 32 00 00 00 00 30 36]
            # Bytes in this packet are:
            #  3: Configuration flags, bits are:
            #    1: direction
            #    2: operation mode
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
                _LOGGER.error(f'[{self}] Problem with moving: NACK')
        elif data[1] in [AM43_REPLY_UNKNOWN1, AM43_REPLY_UNKNOWN2]:
            # [9a a8 00 32]
            # [9a a9 10 00 00 00 11 00 00 00 00 01 00 00 11 00 00 00 00 22]
            pass
        else:
            _LOGGER.error(
                f'{self} BLE notification unknown response '
                f'[{format_binary(data)}]',
            )

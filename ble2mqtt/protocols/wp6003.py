import abc
import asyncio as aio
import datetime
import logging
import uuid

from ble2mqtt.devices.base import BaseDevice
from ble2mqtt.protocols.base import (BaseCommand, BLEQueueMixin,
                                     SendAndWaitReplyMixin)
from ble2mqtt.utils import format_binary

_LOGGER = logging.getLogger(__name__)


CMD_SET_TIME = 0xaa
CMD_SET_NOTIFY_PERIOD = 0xae
CMD_READ_VALUE = 0xab
CMD_REQUEST_CALIBRATION = 0xad
CMD_RESET = 0xee


class WP6003Command(BaseCommand):
    pass


class WP6003Protocol(SendAndWaitReplyMixin, BLEQueueMixin, BaseDevice,
                     abc.ABC):
    RX_CHAR: uuid.UUID = None  # type: ignore
    TX_CHAR: uuid.UUID = None  # type: ignore

    async def protocol_start(self):
        assert self.RX_CHAR
        await self.client.start_notify(
            self.RX_CHAR,
            self.notification_callback,
        )

    async def send_reset(self):
        await self.send_command(bytes([CMD_RESET]), True, timeout=3)

    async def write_time(self):
        now = datetime.datetime.utcnow()
        set_time_cmd = bytes([
            CMD_SET_TIME,
            now.year - 2000,
            now.month,
            now.day,
            now.hour,
            now.minute,
            now.second,
        ])
        # consider process response here
        return await self.send_command(set_time_cmd, True, timeout=3)

    async def read_value(self) -> bytes:
        return await self.send_command(
            bytes([CMD_READ_VALUE]),
            wait_reply=True,
            timeout=3,
        )

    async def send_command(self, cmd: bytes = b'',
                           wait_reply=False, timeout=10):
        command = WP6003Command(cmd, wait_reply=wait_reply, timeout=timeout)
        self.clear_ble_queue()
        await self.cmd_queue.put(command)
        return await aio.wait_for(command.answer, timeout)

    async def process_command(self, command: WP6003Command):
        _LOGGER.debug(f'... send cmd {format_binary(command.cmd)}')
        self.clear_ble_queue()
        cmd_resp = await aio.wait_for(
            self.client.write_gatt_char(self.TX_CHAR, command.cmd),
            timeout=command.timeout,
        )
        if not command.wait_reply:
            if command.answer.cancelled():
                return
            command.answer.set_result(cmd_resp)
            return

        ble_notification = await self.ble_get_notification(command.timeout)

        # extract payload from container
        cmd_resp = bytes(ble_notification[1])
        if command.answer.cancelled():
            return
        command.answer.set_result(cmd_resp)

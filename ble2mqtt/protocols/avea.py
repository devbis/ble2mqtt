import abc
import asyncio as aio
import logging
import struct
import uuid

from ..devices.base import BaseDevice
from ..utils import color_rgb_to_rgbw, color_rgbw_to_rgb, format_binary
from .base import BaseCommand, BLEQueueMixin, SendAndWaitReplyMixin

_LOGGER = logging.getLogger(__name__)

CMD_COLOR = 0x35
CMD_BRIGHTNESS = 0x57
CMD_NAME = 0x58


class AveaCommand(BaseCommand):
    pass


class AveaProtocol(BLEQueueMixin, SendAndWaitReplyMixin, BaseDevice, abc.ABC):
    DATA_CHAR: uuid.UUID = None  # type: ignore

    async def get_device_data(self):
        if self.DATA_CHAR:
            await self.client.start_notify(
                self.DATA_CHAR,
                self.notification_callback,
            )

    def notification_callback(self, sender_handle: int, data: bytearray):
        self.process_data(data)
        super().notification_callback(sender_handle, data)

    async def send_command(self, cmd: bytes = b'',
                           wait_reply=False, timeout=10):
        command = AveaCommand(cmd, wait_reply=wait_reply, timeout=timeout)
        await self.cmd_queue.put(command)
        return await aio.wait_for(command.answer, timeout)

    async def process_command(self, command: AveaCommand):
        _LOGGER.debug(f'... send cmd {format_binary(command.cmd)}')
        self.clear_ble_queue()
        cmd_resp = await aio.wait_for(
            self.client.write_gatt_char(self.DATA_CHAR, command.cmd, True),
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

    @abc.abstractmethod
    def handle_color(self, value):
        """Handle color tuple (r,g,b)"""
        pass

    @abc.abstractmethod
    def handle_brightness(self, value):
        """Handle brightness value"""
        pass

    @abc.abstractmethod
    def handle_name(self, value):
        pass

    def process_data(self, data: bytearray):
        # b'5\x00\x00\x00\x00\x00\xff\x15\x00 \xff>\x00\x00\x00\x16\x00 \x00?'
        if data[0] == CMD_COLOR:
            (
                cur_white, cur_blue, cur_green, cur_red,
                white, blue, green, red,
            ) = struct.unpack('<HHHHHHHH', data[4:])

            rgbw = (
                (red ^ 0x3000),
                (green ^ 0x2000),
                (blue ^ 0x1000),
                white,
            )
            self.handle_color(tuple(x // 16 for x in color_rgbw_to_rgb(*rgbw)))
        elif data[0] == CMD_BRIGHTNESS:
            self.handle_brightness(int.from_bytes(data[1:], 'little') // 16)
        # elif data[0] == CMD_NAME:
        #     self.handle_name(data[1:].decode().strip('\0'))

    async def read_name(self, timeout=3):
        response = await self.send_command(bytes([CMD_NAME]), True, timeout)
        return response[1:].decode(errors='ignore').strip('\0')

    async def read_state(self, timeout=3):
        color_read_cmd = bytes([CMD_COLOR])

        # consider process response here
        await self.send_command(color_read_cmd, True, timeout)
        brightness_read_cmd = bytes([CMD_BRIGHTNESS])
        await self.send_command(brightness_read_cmd, True, timeout)

    @staticmethod
    def convert_value(val):
        # handle PWM if any at max level
        if val <= 0:
            return 0
        elif val < 255:
            return val * 16
        return 4095

    @staticmethod
    def get_color_cmd(w=2000, r=0, g=0, b=0, delay=100):
        """Return the command for the specified colors"""

        fading = delay.to_bytes(2, byteorder='little')
        unknown_magic = bytes([0x0a, 0x00])
        white = (int(w) | int(0x8000)).to_bytes(2, byteorder='little')
        red = (int(r) | int(0x3000)).to_bytes(2, byteorder='little')
        green = (int(g) | int(0x2000)).to_bytes(2, byteorder='little')
        blue = (int(b) | int(0x1000)).to_bytes(2, byteorder='little')

        return (
                bytes([CMD_COLOR]) + fading + unknown_magic +
                white + red + green + blue
        )

    @staticmethod
    def get_brightness_cmd(brightness):
        """Return the command for the specified brightness"""

        return (
                bytes([CMD_BRIGHTNESS]) +
                brightness.to_bytes(2, byteorder='little')
        )

    async def write_color(self, r, g, b):
        _r, _g, _b, _w = color_rgb_to_rgbw(r, g, b)
        cmd = self.get_color_cmd(
            self.convert_value(_w),
            self.convert_value(_r),
            self.convert_value(_g),
            self.convert_value(_b),
        )
        _LOGGER.info(
            f'Writing color ({(_r, _g, _b, _w)}): {format_binary(cmd)}',
        )
        await self.send_command(cmd)

    async def write_brightness(self, brightness):
        cmd = self.get_brightness_cmd(self.convert_value(brightness))
        _LOGGER.info(f'Writing brightness {brightness}: {format_binary(cmd)}')
        await self.send_command(cmd)

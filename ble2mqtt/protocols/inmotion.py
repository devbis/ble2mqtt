import abc
import asyncio as aio
import logging
import re
import struct
from dataclasses import dataclass
from enum import Enum

from ..devices.base import BaseDevice
from ..utils import format_binary
from .base import BaseCommand, BLEQueueMixin, SendAndWaitReplyMixin

_LOGGER = logging.getLogger(__name__)


STANDARD_FORMAT = 0
EXTENDED_FORMAT = 1

DATA_FRAME = 0
REMOTE_FRAME = 1


class InmotionCommandType(Enum):
    NOOP = 0x0
    GET_FAST_INFO = 0x0F550113
    GET_SLOW_INFO = 0x0F550114
    RIDE_MODE = 0x0F550115
    REMOTE_CONTROL = 0x0F550116
    CALIBRATION = 0x0F550119
    PIN_CODE = 0x0F550307
    LIGHT = 0x0F55010D
    HANDLE_BUTTON = 0x0F55012E
    SPEAKER_VOLUME = 0x0F55060A
    PLAY_SOUND = 0x0F550609
    ALERT = 0x0F780101


@dataclass
class InmotionCommand:
    id: InmotionCommandType = InmotionCommandType.NOOP
    ch: int = 5
    type: int = DATA_FRAME
    format: int = STANDARD_FORMAT
    payload: bytes = b'\xff' * 8  # len
    extra_payload: bytes = b''

    def to_bytes(self):
        return struct.pack(
            '<I%dcBB' % len(self.payload),
            self.id.value,
            len(self.payload),
            self.ch,
            self.format,
            self.type,
        ) + self.extra_payload


class InmotionWheelProtocol(SendAndWaitReplyMixin, BLEQueueMixin, BaseDevice,
                            abc.ABC):
    RX_CHAR: uuid.UUID = None  # type: ignore
    TX_CHAR: uuid.UUID = None  # type: ignore

    MAGIC_START = b'\xaa\xaa'
    MAGIC_END = b'\x55\x55'

    async def protocol_start(self):
        assert self.RX_CHAR
        await self.client.start_notify(
            self.RX_CHAR,
            self.notification_callback,
        )

    @staticmethod
    def _escape_command(cmd: bytes):
        return re.sub(b'([\xaa\x55\xa5])', b'\xa5\\1', cmd)

    @staticmethod
    def _get_checksum(cmd: bytes):
        return sum(map(ord, cmd)) % 256

    def _get_command(self, cmd: bytes):
        return bytearray(b'%b%b%c%b' % (
            self.MAGIC_START,
            self._escape_command(cmd),
            self._get_checksum(cmd),
            self.MAGIC_END,
        ))

    def send_command(self, cmd: InmotionCommand, wait_reply=True, timeout=25):
        command = BaseCommand(
            cmd,
            wait_reply=wait_reply,
            timeout=timeout,
        )
        await self.cmd_queue.put(command)
        return await aio.wait_for(command.answer, timeout)

    async def process_command(self, command: BaseCommand):
        cmd = command.cmd.to_bytes()
        _LOGGER.debug(
            f'... send cmd {command.cmd.value:04x} ['
            f'{format_binary(cmd, delimiter="")}]',
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

    async def get_fast_data(self):
        cmd = InmotionCommand(id=InmotionCommandType.GET_FAST_INFO)
        return await self.send_command(cmd)

    async def get_slow_data(self):
        cmd = InmotionCommand(
            id=InmotionCommandType.GET_SLOW_INFO,
            type=REMOTE_FRAME,
        )
        return await self.send_command(cmd)

    async def set_light(self, value: bool):
        cmd = InmotionCommand(
            id=InmotionCommandType.LIGHT,
            payload=bytes([int(value)] + [0] * 7),
        )
        return await self.send_command(cmd)

    async def set_led(self, value: bool):
        cmd = InmotionCommand(
            id=InmotionCommandType.REMOTE_CONTROL,
            payload=bytes([0xb2, 0, 0, 0, 0xf if value else 0x10, 0, 0, 0]),
        )
        return await self.send_command(cmd)

    async def wheel_beep(self):
        cmd = InmotionCommand(
            id=InmotionCommandType.REMOTE_CONTROL,
            payload=bytes([0xb2, 0, 0, 0, 0x11, 0, 0, 0]),
        )
        return await self.send_command(cmd)

    async def power_off(self):
        cmd = InmotionCommand(
            id=InmotionCommandType.REMOTE_CONTROL,
            payload=bytes([0xb2, 0, 0, 0, 0x5, 0, 0, 0]),
        )
        return await self.send_command(cmd)

    async def get_battery_levels_data(self):
        cmd = InmotionCommand(
            id=InmotionCommandType.GET_SLOW_INFO,
            payload=bytes([0, 0, 0, 15, 0, 0, 0, 0]),
            type=REMOTE_FRAME,
        )
        return await self.send_command(cmd)

    async def get_version(self):
        cmd = InmotionCommand(
            id=InmotionCommandType.GET_SLOW_INFO,
            payload=bytes([32, 0, 0, 0, 0, 0, 0, 0]),
            type=REMOTE_FRAME,
        )
        return await self.send_command(cmd)

    def battery_from_voltage(self, volts_i):
        volts = volts_i / 100.0
        pass
        '''
            static int batteryFromVoltage(int volts_i, Model model) {
        double volts = (double)volts_i/100.0;
        double batt;

        if (model.belongToInputType("1") || model == R0) {
            if (volts >= 82.50) {
                batt = 1.0;
            } else if (volts > 68.0) {
                batt = (volts - 68.0) / 14.50;
            } else {
                batt = 0.0;
            }
        } else {
            Boolean useBetterPercents = WheelLog.AppConfig.getUseBetterPercents();
            if (model.belongToInputType("5") || model == Model.V8 || model == Model.Glide3 || model == Model.V8F || model == Model.V8S) {
                if (useBetterPercents) {
                    if (volts > 84.00) {
                        batt = 1.0;
                    } else if (volts > 68.5) {
                        batt = (volts - 68.5) / 15.5;
                    } else {
                        batt = 0.0;
                    }
                } else {
                    if (volts > 82.50) {
                        batt = 1.0;
                    } else if (volts > 68.0) {
                        batt = (volts - 68.0) / 14.5;
                    } else {
                        batt = 0.0;
                    }
                }
            } else if (model == Model.V10 || model == Model.V10F || model == Model.V10S || model == Model.V10SF || model == Model.V10T || model == Model.V10FT) {
                if (useBetterPercents) {
                    if (volts > 83.50) {
                        batt = 1.00;
                    } else if (volts > 68.00) {
                        batt = (volts - 66.50) / 17;
                    } else if (volts > 64.00) {
                        batt = (volts - 64.00) / 45;
                    } else {
                        batt = 0;
                    }
                } else {
                    if (volts > 82.50) {
                        batt = 1.0;
                    } else if (volts > 68.0) {
                        batt = (volts - 68.0) / 14.5;
                    } else {
                        batt = 0.0;
                    }
                }
            } else if (model.belongToInputType("6")) {
                batt = 0.0;
            } else {
                if (volts >= 82.00) {
                    batt = 1.0;
                } else if (volts > 77.8) {
                    batt = ((volts - 77.8) / 4.2) * 0.2 + 0.8;
                } else if (volts > 74.8) {
                    batt = ((volts - 74.8) / 3.0) * 0.2 + 0.6;
                } else if (volts > 71.8) {
                    batt = ((volts - 71.8) / 3.0) * 0.2 + 0.4;
                } else if (volts > 70.3) {
                    batt = ((volts - 70.3) / 1.5) * 0.2 + 0.2;
                } else if (volts > 68.0) {
                    batt = ((volts - 68.0) / 2.3) * 0.2;
                } else {
                    batt = 0.0;
                }
            }
        }
        return (int)(batt * 100.0);
    }
        '''

    def parse_fast_response(self, value):
        voltage = int.from_bytes(value[24:28], byteorder='little')
        battery = self.battery_from_voltage(voltage)
        '''
                if (!isValid()) return false;
            double angle = (double) (MathsUtil.intFromBytesLE(ex_data, 0)) / 65536.0;
            double roll = (double) (MathsUtil.intFromBytesLE(ex_data, 72)) / 90.0;
            double speed = ((double) (MathsUtil.intFromBytesLE(ex_data, 12)) + (double) (MathsUtil.intFromBytesLE(ex_data, 16))) / (model.getSpeedCalculationFactor() * 2.0);
            speed = Math.abs(speed);
            int voltage = MathsUtil.intFromBytesLE(ex_data, 24);
            int current = (int)MathsUtil.signedIntFromBytesLE(ex_data, 20);
            int temperature = ex_data[32];
            int temperature2 = ex_data[34];
            int batt = batteryFromVoltage(voltage, model);
            long totalDistance;
            long distance;
            if (model.belongToInputType("1") || model.belongToInputType("5") ||
                    model == V8 || model == Glide3 || model == V10 || model == V10F ||
                    model == V10S || model == V10SF || model == V10T || model == V10FT ||
                    model == V8F || model == V8S) {
                totalDistance = (MathsUtil.intFromBytesLE(ex_data, 44)); ///// V10F 48 byte - trip distance
            } else if (model == R0) {
                totalDistance = (MathsUtil.longFromBytesLE(ex_data, 44));

            } else if (model == L6) {
                totalDistance = (MathsUtil.longFromBytesLE(ex_data, 44)) * 100;

            } else {
                totalDistance = Math.round((MathsUtil.longFromBytesLE(ex_data, 44)) / 5.711016379455429E7d);
            }
            distance = (MathsUtil.intFromBytesLE(ex_data, 48));

            String workMode;
            int workModeInt = MathsUtil.intFromBytesLE(ex_data, 60);
            if (model == V8F || model == V8S || model == V10 || model == V10F || model == V10FT ||
                    model == V10S || model == V10SF || model == V10T) {
                roll = 0;
                workMode = getWorkModeString(workModeInt);
            } else {
                workMode = getLegacyWorkModeString(workModeInt);
            }

            WheelData wd = WheelData.getInstance();
            wd.setAngle(angle);
            wd.setRoll(roll);
            wd.setSpeed((int)(speed * 360d));
            wd.setVoltage(voltage);
            wd.setBatteryLevel(batt);
            wd.setCurrent(current);
            wd.setTotalDistance(totalDistance);
            wd.setWheelDistance(distance);
            wd.setTemperature(temperature*100);
            wd.setTemperature2(temperature2*100);
            wd.setModeStr(workMode);

            return true;
        '''

    def parse_slow_response(self, value):
        pass

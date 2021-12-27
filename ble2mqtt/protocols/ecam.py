import abc
import asyncio as aio
import logging
import struct
import typing as ty
import uuid
from enum import Enum

from ble2mqtt.devices.base import BaseDevice
from ble2mqtt.protocols.base import (BaseCommand, BLEQueueMixin,
                                     SendAndWaitReplyMixin)
from ble2mqtt.utils import format_binary

_LOGGER = logging.getLogger(__name__)

ECAMRequestHeader = 0x0d
ECAMResponseHeader = 0xd0

BEVERAGE_DISPENSING_ANSWER_ID = 0x83


class ECAMCommand(BaseCommand):
    pass


# turnOnMode

class CoffeeTypes(Enum):
    ESPRESSO_COFFEE = 1
    REGULAR_COFFEE = 2
    LONG_COFFEE = 3
    ESPRESSO_COFFEE_2X = 4
    DOPPIO_PLUS = 5
    AMERICANO = 6
    CAPPUCCINO = 7
    LATTE_MACCHIATO = 8
    CAFFE_LATTE = 9
    FLAT_WHITE = 10
    ESPRESSO_MACCHIATO = 11
    HOT_MILK = 12
    CAPPUCCINO_DOPPIO_PLUS = 13
    COLD_MILK = 14
    CAPPUCCINO_REVERSE = 15
    HOT_WATER = 16
    STEAM = 17
    CIOCCO = 18
    CUSTOM_01 = 19
    CUSTOM_02 = 20
    CUSTOM_03 = 21
    CUSTOM_04 = 22
    CUSTOM_05 = 23
    CUSTOM_06 = 24
    RINSING = 25
    DESCALING = 26


class IngredientsType(Enum):
    TEMP = 0
    WATER = 1
    TASTE = 2
    GRANULOMETRY = 3
    BLEND = 4
    AROMA = 5
    PREINFUSIONE = 6
    CREMA = 7
    DUExPER = 8
    MILK = 9
    MILK_TEMP = 10
    MILK_FROTH = 11
    INVERSION = 12
    THE_TEMP = 13
    THE_PROFILE = 14
    HOT_WATER = 15
    MIX_VELOCITY = 16
    MIX_DURATION = 17
    DENSITY_MULTI_BEVERAGE = 18
    TEMP_MULTI_BEVERAGE = 19
    DECALC_TYPE = 20
    TEMP_RISCIACQUO = 21
    WATER_RISCIACQUO = 22
    CLEAN_TYPE = 23
    PROGRAMABLE = 24
    VISIBLE = 25
    VISIBLE_IN_PROGRAMMING = 26
    INDEX_LENGTH = 27
    ACCESSORIO = 28


class CoffeeTemperature(Enum):
    LOW = 0
    MID = 1
    HIGH = 2
    VERY_HIGH = 3


class ECAMAlarmType(Enum):
    EMPTY_WATER_TANK = (0, 0)
    COFFEE_WASTE_CONTAINER_FULL = (0, 1)
    DESCALE_ALARM = (0, 2)
    REPLACE_WATER_FILTER = (0, 3)
    COFFE_GROUND_TOO_FINE = (0, 4)
    COFFEE_BEANS_EMPTY = (0, 5)
    MACHINE_TO_SERVICE = (0, 6)
    COFFEE_HEATER_PROBE_FAILURE = (0, 7)
    TOO_MUCH_COFFEE = (1, 0)
    COFFEE_INFUSER_MOTOR_NOT_WORKING = (1, 1)
    STEAMER_PROBE_FAILURE = (1, 2)
    EMPTY_DRIP_TRAY = (1, 3)
    HYDRAULIC_CIRCUIT_PROBLEM = (1, 4)
    TANK_IS_IN_POSITION = (1, 5)
    CLEAN_KNOB = (1, 6)
    COFFEE_BEANS_EMPTY_TWO = (1, 7)
    TANK_TOO_FULL = (2, 0)
    BEAN_HOPPER_ABSENT = (2, 1)
    GRID_PRESENCE = (2, 2)
    INFUSER_SENSE = (2, 3)
    NOT_ENOUGH_COFFEE = (2, 4)
    EXPANSION_COMM_PROB = (2, 5)
    EXPANSION_SUBMODULES_PROB = (2, 6)
    GRINDING_UNIT_1_PROBLEM = (2, 7)
    GRINDING_UNIT_2_PROBLEM = (3, 0)
    CONDENSE_FAN_PROBLEM = (3, 1)
    CLOCK_BT_COMM_PROBLEM = (3, 2)
    SPI_COMM_PROBLEM = (3, 3)
    UNKNOWN_ALARM = (99, 99)
    IGNORE_ALARM = (100, 100)

    @classmethod
    def from_bit_num(cls, value: int):
        return {
            0: cls.EMPTY_WATER_TANK,
            1: cls.COFFEE_WASTE_CONTAINER_FULL,
            2: cls.DESCALE_ALARM,
            3: cls.REPLACE_WATER_FILTER,
            4: cls.COFFE_GROUND_TOO_FINE,
            5: cls.COFFEE_BEANS_EMPTY,
            6: cls.MACHINE_TO_SERVICE,
            7: cls.COFFEE_HEATER_PROBE_FAILURE,
            8: cls.TOO_MUCH_COFFEE,
            9: cls.IGNORE_ALARM,
            10: cls.STEAMER_PROBE_FAILURE,
            11: cls.EMPTY_DRIP_TRAY,
            12: cls.HYDRAULIC_CIRCUIT_PROBLEM,
            13: cls.IGNORE_ALARM,
            14: cls.CLEAN_KNOB,
            15: cls.COFFEE_BEANS_EMPTY_TWO,
            16: cls.IGNORE_ALARM,
            17: cls.BEAN_HOPPER_ABSENT,
            18: cls.GRID_PRESENCE,
            19: cls.INFUSER_SENSE,
            20: cls.IGNORE_ALARM,
            21: cls.IGNORE_ALARM,
            22: cls.EXPANSION_SUBMODULES_PROB,
            23: cls.IGNORE_ALARM,
            24: cls.IGNORE_ALARM,
            25: cls.CONDENSE_FAN_PROBLEM,
            26: cls.IGNORE_ALARM,
            27: cls.IGNORE_ALARM,
            28: cls.IGNORE_ALARM,
            29: cls.IGNORE_ALARM,
            30: cls.IGNORE_ALARM,
            31: cls.IGNORE_ALARM,
        }.get(value, cls.UNKNOWN_ALARM)


class BeverageTasteType(Enum):
    DELETE_BEVERAGE = 0
    SAVE_BEVERAGE = 1
    PREPARE_BEVERAGE = 2
    PREPARE_AND_SAVE_BEVERAGE = 3
    SAVE_BEVERAGE_INVERSION = 5
    PREPARE_BEVERAGE_INVERSION = 6
    PREPARE_SAVE_BEVERAGE_INVERSION = 7


class BeverageTasteValue(Enum):
    PREGROUND = 0
    EXTRA_MILD = 6
    MILD = 2
    NORMAL = 8
    STRONG = 4
    EXTRA_STRONG = 0
    DELETE = 2


class ECAMCommandTypes(Enum):
    TURN_ON = b'\x84\x0f\x02\x01'  # \x55\x12 - checksum
    GET_STATE = b'\x75\x0f'
    BEVERAGE_ESPRESSO = b'\x83\xf0\x01\x01\x01\x00\x28\x02\x03\x08\x00\x00\x00\x06'
    BEVERAGE_COFFEE = b'\x83\xf0\x02\x01\x01\x00\x67\x02\x02\x00\x00\x06'
    BEVERAGE_DOUBLE_ESPRESSO = b'\x83\xf0\x04\x01\x01\x00\x28\x02\x02\x00\x00\x06'
    BEVERAGE_DOPPIO = b'\x83\xf0\x05\x01\x01\x00\x78\x00\x00\x06'
    BEVERAGE_HOTWATER = b'\x83\xf0\x10\x01\x0f\x00\xfa\x1c\x01\x06'
    BEVERAGE_STEAM = b'\x83\xf0\x11\x01\x09\x03\x84\x1c\x01\x06'
    BEVERAGE_AMERICANO = b'\x83\xf0\x06\x01\x01\x00\x28\x02\x03\x0f\x00\x6e\x00\x00\x06'
    BEVERAGE_COFFEE_LONG = b'\x83\xf0\x03\x01\x01\x00\xa0\x02\x03\x00\x00\x06'


class OperationTriggerId(Enum):
    DONTCARE = 0
    START = 1
    START_PROGRAM = 2
    NEXT_STEP = 3
    STOP = 4
    STOP_PROGRAM = 5
    EXIT_PROGRAM_OK = 6
    STOPV2 = 2


def get_beverage_cmd(type: CoffeeTypes, mask: int,
                     op_trigger: OperationTriggerId,
                     parameters: list,
                     taste_value,
                     taste_type: BeverageTasteType,
                     ):
    _ = b'\x83\xf0\x01\x01' \
        b'\x01\x00\x28\x02\x03\x08\x00\x00\x00\x06'
    return bytes([
        BEVERAGE_DISPENSING_ANSWER_ID,
        0xf0,
        type.value,
        op_trigger.value,

    ])


class MachineSwitch(Enum):
    WATER_SPOUT = (0, 0)
    MOTOR_UP = (0, 1)
    MOTOR_DOWN = (0, 2)
    COFFEE_WASTE_CONTAINER = (0, 3)
    WATER_TANK_ABSENT = (0, 4)
    KNOB = (0, 5)
    WATER_LEVEL_LOW = (0, 6)
    COFFEE_JUG = (0, 7)
    IFD_CARAFFE = (1, 0)
    CIOCCO_TANK = (1, 1)
    CLEAN_KNOB = (1, 2)
    DOOR_OPENED = (1, 5)
    PREGROUND_DOOR_OPENED = (1, 6)
    UNKNOWN_SWITCH = (99, 99)
    IGNORE_SWITCH = (100, 100)

    @classmethod
    def from_bit_num(cls, value):
        return {
            0: cls.WATER_SPOUT,
            1: cls.IGNORE_SWITCH,
            2: cls.IGNORE_SWITCH,
            3: cls.COFFEE_WASTE_CONTAINER,
            4: cls.WATER_TANK_ABSENT,
            5: cls.KNOB,
            6: cls.IGNORE_SWITCH,
            7: cls.COFFEE_JUG,
            8: cls.IFD_CARAFFE,
            9: cls.CIOCCO_TANK,
            10: cls.CLEAN_KNOB,
            11: cls.IGNORE_SWITCH,
            12: cls.IGNORE_SWITCH,
            13: cls.DOOR_OPENED,
            14: cls.PREGROUND_DOOR_OPENED,
        }.get(value, cls.UNKNOWN_SWITCH)


def bits(number):
    bit = 1
    while number >= bit:
        if number & bit:
            yield bit
        bit <<= 1


def parse_state(data: bytes):
    type = data[2]
    # if type == 0x90:
    #     pass
    # elif type == 0x95:
    #     pass
    if type == 0x75:
        var = 2
    elif type == 0x70:
        var = 1
    elif type == 0x60:
        var = 0
    else:
        raise NotImplementedError(f'Unknown notificaiton type :{type:x}')

    on_switches = None
    if var != 1:
        bitmask = int.from_bytes(data[5:7], 'little')
        on_switches = [MachineSwitch.from_bit_num(b) for b in bits(bitmask)]

    alarms = []
    if var != 0:
        offset = 0 if var == 1 else 3
        bitmask = int.from_bytes(
            data[4+offset:6+offset] + data[12:14], 'little')
        alarms = [ECAMAlarmType.from_bit_num(b) for b in bits(bitmask)]

    ongoing = None
    if var != 0:
        offset = 0 if var == 1 else 1
        ongoing = data[8+offset]
    execution_progress = None
    if var != 0:
        offset = 0 if var == 1 else 1
        execution_progress = data[9+offset]

    return {
        'on_switches': on_switches,
        'alarms': alarms,
        'ongoing': ongoing,
        'execution_progress': execution_progress,
    }

    """
        private void m962d(byte[] bArr) {
        boolean z = true;
        int i = 0;
        byte b = bArr[2];
        switch (b) {
            case -112:
                short a = C0645m.m1563a(bArr[4], bArr[5]);
                if (bArr[6] != 0) {
                    z = false;
                }
                this.f766h.mo6401b((int) a, z);
                return;
            case -107:
            case -95:
                int i2 = ((bArr[1] & 255) - 5) / 4;
                ArrayList arrayList = new ArrayList(i2);
                int a2 = C0645m.m1563a(bArr[4], bArr[5]);
                for (int i3 = 0; i3 < i2; i3++) {
                    byte[] bArr2 = new byte[4];
                    System.arraycopy(bArr, (i3 * 4) + 6, bArr2, 0, bArr2.length);
                    arrayList.add(new Parameter(a2, bArr2));
                    a2++;
                }
                this.f766h.mo6397a(arrayList);
                return;
            case -93:
                short a3 = C0645m.m1563a(bArr[18], bArr[19]);
                short a4 = C0645m.m1563a(bArr[16], bArr[17]);
                short[] sArr = new short[6];
                while (i < 12) {
                    sArr[i / 2] = C0645m.m1563a(bArr[i + 4], bArr[i + 5]);
                    i += 2;
                }
                this.f766h.mo6399a(a3, a4, sArr);
                return;
            case -92:
            case -86:
                int a5 = (C0645m.m1542a(bArr[1]) - 4) / 21;
                ArrayList arrayList2 = new ArrayList(a5);
                ArrayList arrayList3 = new ArrayList(a5);
                for (int i4 = 0; i4 < a5; i4++) {
                    byte[] bArr3 = new byte[20];
                    System.arraycopy(bArr, (i4 * 21) + 4, bArr3, 0, bArr3.length);
                    String str = null;
                    if (!C0645m.m1583b(bArr3)) {
                        str = C0645m.m1558a(C0645m.m1591c(bArr3), "UTF-16");
                    }
                    Integer valueOf = Integer.valueOf(C0645m.m1542a(bArr[(i4 * 21) + 24]));
                    arrayList2.add(str);
                    arrayList3.add(valueOf);
                }
                if (b == -92) {
                    this.f766h.mo6398a(arrayList2, arrayList3);
                    return;
                } else {
                    this.f766h.mo6403b(arrayList2, arrayList3);
                    return;
                }
            case -91:
                if (bArr[4] != 0) {
                    z = false;
                }
                this.f766h.mo6404b(z);
                return;
            case -90:
                int i5 = ((bArr[1] & 255) - 4) / 5;
                int c = C0645m.m1586c(bArr[4]);
                ArrayList arrayList4 = new ArrayList(24);
                for (int i6 = 0; i6 < i5; i6++) {
                    int i7 = i6 * 5;
                    short a6 = C0645m.m1563a(bArr[i7 + 5], bArr[i7 + 6]);
                    short a7 = C0645m.m1563a(bArr[i7 + 7], bArr[i7 + 8]);
                    byte b2 = bArr[i7 + 9] & 240;
                    boolean z2 = (bArr[i7 + 9] & 4) != 0;
                    C0500c b3 = C0500c.m1118b(b2);
                    RecipeData recipeData = new RecipeData(C0498a.m1114a(i6 + 1));
                    recipeData.mo6498a((int) a6);
                    recipeData.mo6503b((int) a7);
                    recipeData.mo6499a(b3);
                    recipeData.mo6504b(z2);
                    arrayList4.add(recipeData);
                }
                this.f766h.mo6391a(c, arrayList4);
                return;
            case -88:
                int a8 = C0645m.m1542a(bArr[4]);
                int[] iArr = new int[24];
                while (i < 24) {
                    iArr[i] = C0645m.m1542a(bArr[i + 5]);
                    i++;
                }
                this.f766h.mo6393a(a8, iArr);
                return;
            case -87:
                if (bArr[5] != 0) {
                    z = false;
                }
                this.f766h.mo6392a((int) bArr[4], z);
                return;
            case -85:
                if (bArr[4] != 0) {
                    z = false;
                }
                this.f766h.mo6400a(z);
                return;
            case -31:
                this.f766h.mo6390a((int) b);
                return;
            case 96:
                this.f766h.mo6394a(new MonitorData(0, bArr));
                return;
            case 112:
                this.f766h.mo6394a(new MonitorData(1, bArr));
                return;
            case 117:
                this.f766h.mo6394a(new MonitorData(2, bArr));
                return;
            default:
                return;
        }
    }
    """


class ECAMProtocol(BLEQueueMixin, SendAndWaitReplyMixin, BaseDevice, abc.ABC):
    DATA_CHAR: uuid.UUID

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    async def protocol_start(self):
        assert self.DATA_CHAR
        await self.client.start_notify(
            self.DATA_CHAR,
            self.notification_callback,
        )

    async def send_command(self, cmd: bytes = b'',
                           wait_reply=False, timeout=10):
        command = ECAMCommand(cmd, wait_reply=wait_reply, timeout=timeout)
        await self.cmd_queue.put(command)
        return await aio.wait_for(command.answer, timeout)

    async def process_command(self, command: ECAMCommand):
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

    @staticmethod
    def get_checksum(data: bytes):
        crc = 0x1d0f
        for i in range(0, len(data)):
            crc ^= data[i] << 8
            for _ in range(0, 8):
                if (crc & 0x8000) > 0:
                    crc = (crc << 1) ^ 0x1021
                else:
                    crc = crc << 1
        return (crc & 0xFFFF).to_bytes(2, byteorder='big')

    @staticmethod
    def get_checksum1(data: bytes) -> bytes:
        checksum = 0x1d0f
        for byte in data:
            i3 = (((checksum << 8) | (checksum >> 8)) & 0x0000ffff) ^ \
                 (byte & 0xffff)
            i4 = i3 ^ ((i3 & 0xff) >> 4)
            i5 = i4 ^ ((i4 << 12) & 0x0000ffff)
            checksum = i5 ^ (((i5 & 0xff) << 5) & 0x0000ffff)
        return struct.pack('>H', checksum & 0x0000ffff)

    def _get_command_bytes(self, value: bytes):
        data = bytearray([ECAMRequestHeader, len(value) + 3]) + value
        checksum = self.get_checksum(data)
        return data + checksum

    async def power_on(self):
        await self.send_command(
            self._get_command_bytes(ECAMCommandTypes.TURN_ON.value),
        )

    async def get_state(self) -> ty.Optional[tuple]:
        response = await self.send_command(
            self._get_command_bytes(ECAMCommandTypes.GET_STATE.value)
        )
        if response[0] != ECAMResponseHeader:
            return None
        length = response[1]
        if length != 0x12:
            return None
        if response[2:4] != ECAMCommandTypes.GET_STATE.value:
            return None

        state = struct.unpack('>14B', response[4:-2])
        return state

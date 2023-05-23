import abc
import asyncio as aio
import logging
import struct
import uuid

from ..compat import get_loop_param
from ..devices.base import Sensor, SubscribeAndSetDataMixin

_LOGGER = logging.getLogger(__name__)


# Xiaomi Humidity/Temperature sensors

class XiaomiPoller(SubscribeAndSetDataMixin, Sensor, abc.ABC):
    DATA_CHAR: uuid.UUID = None  # type: ignore
    BATTERY_CHAR: uuid.UUID = None  # type: ignore
    MANUFACTURER = 'Xiaomi'

    def __init__(self, *args, loop, **kwargs):
        super().__init__(*args, loop=loop, **kwargs)
        self._stack = aio.LifoQueue(**get_loop_param(loop))

    def process_data(self, data):
        self._loop.call_soon_threadsafe(self._stack.put_nowait, data)

    async def read_and_send_data(self, publish_topic):
        raise NotImplementedError()

    async def handle_active(self, publish_topic, send_config, *args, **kwargs):
        _LOGGER.debug(f'Wait {self} for connecting...')
        sec_to_wait_connection = 0
        while True:
            if not self.client.is_connected:
                if sec_to_wait_connection >= 30:
                    raise TimeoutError(
                        f'{self} not connected for 30 sec in handle()',
                    )
                sec_to_wait_connection += self.NOT_READY_SLEEP_INTERVAL
                await aio.sleep(self.NOT_READY_SLEEP_INTERVAL)
                continue
            try:
                _LOGGER.debug(f'{self} connected!')
                # in case of bluetooth error populating queue
                # could stop and will wait for self._stack.get() forever
                await self.update_device_data(send_config)
                await aio.wait_for(
                    self.read_and_send_data(publish_topic),
                    timeout=15,
                )
            except ValueError as e:
                _LOGGER.error(f'[{self}] Cannot read values {str(e)}')
            else:
                await self.close()
                return
            await aio.sleep(1)


# Xiaomi Kettles

class XiaomiCipherMixin:
    # Picked from the https://github.com/drndos/mikettle/
    @staticmethod
    def generate_random_token() -> bytes:
        return bytes([  # from component, maybe random is ok
            0x01, 0x5C, 0xCB, 0xA8, 0x80, 0x0A, 0xBD, 0xC1, 0x2E, 0xB8,
            0xED, 0x82,
        ])
        # return os.urandom(12)

    @staticmethod
    def reverse_mac(mac) -> bytes:
        parts = mac.split(":")
        reversed_mac = bytearray()
        length = len(parts)
        for i in range(1, length + 1):
            reversed_mac.extend(bytearray.fromhex(parts[length - i]))
        return reversed_mac

    @staticmethod
    def mix_a(mac, product_id) -> bytes:
        return bytes([
            mac[0], mac[2], mac[5], (product_id & 0xff), (product_id & 0xff),
            mac[4], mac[5], mac[1],
        ])

    @staticmethod
    def mix_b(mac, product_id) -> bytes:
        return bytes([
            mac[0], mac[2], mac[5], ((product_id >> 8) & 0xff), mac[4], mac[0],
            mac[5], (product_id & 0xff),
        ])

    @staticmethod
    def _cipher_init(key) -> bytes:
        perm = bytearray()
        for i in range(0, 256):
            perm.extend(bytes([i & 0xff]))
        keyLen = len(key)
        j = 0
        for i in range(0, 256):
            j += perm[i] + key[i % keyLen]
            j = j & 0xff
            perm[i], perm[j] = perm[j], perm[i]
        return perm

    @staticmethod
    def _cipher_crypt(input, perm) -> bytes:
        index1 = 0
        index2 = 0
        output = bytearray()
        for i in range(0, len(input)):
            index1 = index1 + 1
            index1 = index1 & 0xff
            index2 += perm[index1]
            index2 = index2 & 0xff
            perm[index1], perm[index2] = perm[index2], perm[index1]
            idx = perm[index1] + perm[index2]
            idx = idx & 0xff
            output_byte = input[i] ^ perm[idx]
            output.extend(bytes([output_byte & 0xff]))

        return output

    @classmethod
    def cipher(cls, key, input) -> bytes:
        perm = cls._cipher_init(key)
        return cls._cipher_crypt(input, perm)


# region xiaomi advert parsers from 0xfe95

# this part is partly taken from ble_monitor component for Home Assistant

# Structured objects for data conversions
TH_STRUCT = struct.Struct("<hH")
H_STRUCT = struct.Struct("<H")
T_STRUCT = struct.Struct("<h")
TTB_STRUCT = struct.Struct("<hhB")
CND_STRUCT = struct.Struct("<H")
ILL_STRUCT = struct.Struct("<I")
LIGHT_STRUCT = struct.Struct("<I")
FMDH_STRUCT = struct.Struct("<H")
M_STRUCT = struct.Struct("<L")
P_STRUCT = struct.Struct("<H")
BUTTON_STRUCT = struct.Struct("<BBB")
FLOAT_STRUCT = struct.Struct("<f")


def obj0010(xobj):
    # Toothbrush
    if xobj[0] == 0:
        return {'toothbrush': 1, 'counter': xobj[1]}
    else:
        return {'toothbrush': 0, 'score': xobj[1]}


def obj1004(xobj):
    # Temperature
    if len(xobj) == 2:
        (temp,) = T_STRUCT.unpack(xobj)
        return {"temperature": temp / 10}
    else:
        return {}


def obj1005(xobj):
    return {"switch": xobj[0], "temperature": xobj[1]}


def obj1006(xobj):
    # Humidity
    if len(xobj) == 2:
        (humi,) = H_STRUCT.unpack(xobj)
        return {"humidity": humi / 10}
    else:
        return {}


def obj1007(xobj):
    # Illuminance
    if len(xobj) == 3:
        (illum,) = ILL_STRUCT.unpack(xobj + b'\x00')
        return {"illuminance": illum, "light": 1 if illum == 100 else 0}
    else:
        return {}


def obj1008(xobj):
    # Moisture
    return {"moisture": xobj[0]}


def obj1009(xobj):
    # Conductivity
    if len(xobj) == 2:
        (cond,) = CND_STRUCT.unpack(xobj)
        return {"conductivity": cond}
    else:
        return {}


def obj1010(xobj):
    # Formaldehyde
    if len(xobj) == 2:
        (fmdh,) = FMDH_STRUCT.unpack(xobj)
        return {"formaldehyde": fmdh / 100}
    else:
        return {}


def obj1012(xobj):
    # Switch
    return {"switch": xobj[0]}


def obj1013(xobj):
    # Consumable (in percent)
    return {"consumable": xobj[0]}


def obj1014(xobj):
    # Moisture
    return {"moisture": xobj[0]}


def obj1015(xobj):
    # Smoke
    return {"smoke detector": xobj[0]}


def obj1017(xobj):
    # Motion
    if len(xobj) == 4:
        (motion,) = M_STRUCT.unpack(xobj)
        # seconds since last motion detected message
        # (not used, we use motion timer in obj000f)
        # 0 = motion detected
        return {"motion": 1 if motion == 0 else 0}
    else:
        return {}


def obj1018(xobj):
    # Light intensity
    return {"light": xobj[0]}


def obj100a(xobj):
    # Battery
    batt = xobj[0]
    volt = 2.2 + (3.1 - 2.2) * (batt / 100)
    return {"battery": batt, "voltage": volt}


def obj100d(xobj):
    # Temperature and humidity
    if len(xobj) == 4:
        (temp, humi) = TH_STRUCT.unpack(xobj)
        return {"temperature": temp / 10, "humidity": humi / 10}
    else:
        return {}


# The following data objects are device specific.
# For now only added for XMWSDJ04MMC
# https://miot-spec.org/miot-spec-v2/instances?status=all
def obj4803(xobj):
    # Battery
    batt = xobj[0]
    return {"battery": batt}


def obj4c01(xobj):
    if len(xobj) == 4:
        temp = FLOAT_STRUCT.unpack(xobj)[0]
        return {"temperature": temp}
    else:
        return {}


def obj4c08(xobj):
    if len(xobj) == 4:
        humi = FLOAT_STRUCT.unpack(xobj)[0]
        return {"humidity": humi}
    else:
        return {}


# Dataobject dictionary
# {dataObject_id: (converter}
xiaomi_dataobject_dict = {
    # 0x0003: obj0003,
    # 0x0006: obj0006,
    0x0010: obj0010,
    # 0x000B: obj000b,
    # 0x000F: obj000f,
    # 0x1001: obj1001,
    0x1004: obj1004,
    0x1005: obj1005,
    0x1006: obj1006,
    0x1007: obj1007,
    0x1008: obj1008,
    0x1009: obj1009,
    0x1010: obj1010,
    0x1012: obj1012,
    0x1013: obj1013,
    0x1014: obj1014,
    0x1015: obj1015,
    0x1017: obj1017,
    0x1018: obj1018,
    # 0x1019: obj1019,
    0x100A: obj100a,
    0x100D: obj100d,
    # 0x2000: obj2000,
    0x4803: obj4803,
    0x4c01: obj4c01,
    0x4c08: obj4c08,
}


def parse_fe95_advert(adv_data: bytes) -> dict:
    frctrl = int.from_bytes(adv_data[:2], byteorder='little')

    # frctrl_mesh = (frctrl >> 7) & 1  # mesh device
    # frctrl_version = frctrl >> 12  # version
    # frctrl_auth_mode = (frctrl >> 10) & 3
    # frctrl_solicited = (frctrl >> 9) & 1
    # frctrl_registered = (frctrl >> 8) & 1
    # frctrl_object_include = (frctrl >> 6) & 1
    frctrl_capability_include = (frctrl >> 5) & 1
    frctrl_mac_include = (frctrl >> 4) & 1  # check for MAC address in data
    # frctrl_is_encrypted = (frctrl >> 3) & 1  # check for encryption being used
    # frctrl_request_timing = frctrl & 1  # old version

    counter = 5
    if frctrl_mac_include:
        counter += 6
    # check for capability byte present
    if frctrl_capability_include:
        counter += 1
        # capability_io = adv_data[counter - 1]

    payload = adv_data[counter:]
    result = {}
    if payload:
        payload_start = 0
        payload_length = len(payload)
        while payload_length >= payload_start + 3:
            obj_typecode = \
                payload[payload_start] + (payload[payload_start + 1] << 8)
            obj_length = payload[payload_start + 2]
            next_start = payload_start + 3 + obj_length
            if payload_length < next_start:
                _LOGGER.debug(
                    "Invalid payload data length, payload: %s", payload.hex(),
                )
                break
            object = payload[payload_start + 3:next_start]
            if obj_length != 0:
                resfunc = xiaomi_dataobject_dict.get(obj_typecode, None)
                if resfunc:
                    # if hex(obj_typecode) in ["0x1001", "0xf"]:
                    #     result.update(resfunc(object, device_type))
                    # else:
                    result.update(resfunc(object))
                else:
                    # if self.report_unknown == "Xiaomi":
                    _LOGGER.info(
                        "UNKNOWN dataobject in payload! Adv: %s",
                        adv_data.hex(),
                    )
            payload_start = next_start

    return result

# endregion

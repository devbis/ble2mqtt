import io
import logging
import struct
import typing as ty
import uuid
from dataclasses import dataclass

from bleak.backends.device import BLEDevice

from ..protocols.xiaomi import parse_fe95_advert
from ..utils import format_binary
from .base import ConnectionMode
from .uuids import BATTERY
from .xiaomi_base import XiaomiHumidityTemperature

_LOGGER = logging.getLogger(__name__)

SERVICE_DATA_UUID = 'fdcd'

MAC_START = 2
MAC_END = 8


@dataclass
class SensorState:
    battery: int = 0
    temperature: float = 0
    humidity: float = 0
    mac: str = ""


class QingpingTempRHMonitorLite(XiaomiHumidityTemperature):
    NAME = 'qingpingCGDK2'
    MANUFACTURER = 'Qingping'
    DATA_CHAR = SERVICE_DATA_UUID
    BATTERY_CHAR = BATTERY
    SENSOR_CLASS = SensorState
    SUPPORT_PASSIVE = True
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_POLL_WITH_DISCONNECT

    def filter_notifications(self, sender):
        return True

    def handle_advert(self, scanned_device: BLEDevice, adv_data):
        service_data = adv_data.service_data
        adv_data = service_data.get(str("0000fdcd-0000-1000-8000-00805f9b34fb"))
        data_str = format_binary(adv_data," ")
        
        # adv_data = bytes(adv_data)

        preeamble = "cdfd88"
        packetStart = data_str.find(preeamble)
        offset = packetStart + len(preeamble)
        strippedData_str = data_str[6:len(data_str)]
        mac = ':'.join(list(reversed(strippedData_str[0:17].split(' '))))
        dataIdentifier = data_str[(offset-2):offset].upper()

        if dataIdentifier == "10":
                data = io.BytesIO(adv_data)
                data.seek(10)
                temperature = int(format(struct.unpack("<H", data.read(2))[0], '02x'), base=16) /10
                data.seek(12)
                humidity = int(format(struct.unpack("<H", data.read(2))[0], '02x'), base=16) /10
                data.seek(16)
                batteryPercent = int(format_binary(data.read(2)),base=16)
                self._state = self.SENSOR_CLASS(
                    temperature=temperature,
                    humidity=humidity,
                    battery=batteryPercent,
                    mac=mac
                )

        

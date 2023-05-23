import logging
import uuid
from dataclasses import dataclass

from .base import ConnectionMode
from .uuids import BATTERY
from .xiaomi_base import XiaomiHumidityTemperature

_LOGGER = logging.getLogger(__name__)

SERVICE_DATA_UUID = uuid.UUID('0000fdcd-0000-1000-8000-00805f9b34fb')


@dataclass
class SensorState:
    battery: int = 0
    temperature: float = 0.0
    humidity: float = 0.0


class QingpingTempRHMonitorLite(XiaomiHumidityTemperature):
    NAME = 'qingpingCGDK2'
    MANUFACTURER = 'Qingping'
    DATA_CHAR = SERVICE_DATA_UUID
    BATTERY_CHAR = BATTERY
    SENSOR_CLASS = SensorState
    SUPPORT_PASSIVE = True
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_POLL_WITH_DISCONNECT

    PREAMBLE = b'\xcd\xfd'

    def filter_notifications(self, sender, data):
        packet_start = data.find(self.PREAMBLE)
        if packet_start == -1:
            return False
        return data[packet_start + len(self.PREAMBLE) + 1] == 0x10

    def process_data(self, data):
        packet_start = data.find(self.PREAMBLE)
        offset = packet_start + len(self.PREAMBLE)
        value_data = data[offset:]

        self._state = self.SENSOR_CLASS(
            temperature=int.from_bytes(
                value_data[10:12],
                byteorder='little',
                signed=True,
            ) / 10,
            humidity=int.from_bytes(
                value_data[12:14],
                byteorder='little',
                signed=False,
            ) / 10,
            battery=value_data[16],
        )

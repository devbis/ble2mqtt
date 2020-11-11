import logging
import uuid
from dataclasses import dataclass

from .base import Device
from .xiaomi_base import XiaomiHumidityTemperature

logger = logging.getLogger(__name__)

MJHT_DATA = uuid.UUID('226caa55-6476-4566-7562-66734470666d')
MJHT_BATTERY = uuid.UUID('00002a19-0000-1000-8000-00805f9b34fb')


@dataclass
class SensorState:
    battery: int
    temperature: float
    humidity: float

    @classmethod
    def from_data(cls, sensor_data, battery_data):
        # b'T=23.6 H=39.6\x00'
        t, h = tuple(
            float(x.split('=')[1])
            for x in sensor_data.decode().strip('\0').split(' ')
        )
        return cls(
            temperature=t,
            humidity=h,
            battery=int(ord(battery_data)),
        )


class XiaomiHumidityTemperatureV1(XiaomiHumidityTemperature, Device):
    NAME = 'xiaomihtv1'
    REQUIRE_CONNECTION = True
    DATA_CHAR = MJHT_DATA
    BATTERY_CHAR = MJHT_BATTERY
    SENSOR_CLASS = SensorState

    def filter_notifications(self, sender):
        # sender is 0xd or several requests it becomes
        # /org/bluez/hci0/dev_58_2D_34_32_E0_69/service000c/char000d
        return (
            sender == 0xd or
            isinstance(sender, str) and sender.endswith('000d')
        )

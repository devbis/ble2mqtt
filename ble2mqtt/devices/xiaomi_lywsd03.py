import logging
import struct
import uuid
from dataclasses import dataclass

from ..utils import cr2032_voltage_to_percent
from .base import Device
from .xiaomi_base import XiaomiHumidityTemperature

logger = logging.getLogger(__name__)

LYWSD_DATA = uuid.UUID('EBE0CCC1-7A0A-4B0C-8A1A-6FF2997DA3A6')
LYWSD_BATTERY = uuid.UUID('EBE0CCC4-7A0A-4B0C-8A1A-6FF2997DA3A6')


@dataclass
class SensorState:
    battery: int
    temperature: float
    humidity: float

    @classmethod
    def from_data(cls, sensor_data, battery_data):
        t, h, voltage = struct.unpack('<hBH', sensor_data)
        return cls(
            temperature=round(t/100, 2),
            humidity=h,
            battery=int(cr2032_voltage_to_percent(voltage)),
        )


class XiaomiHumidityTemperatureLYWSD(XiaomiHumidityTemperature, Device):
    NAME = 'xiaomilywsd'
    DATA_CHAR = LYWSD_DATA
    BATTERY_CHAR = LYWSD_BATTERY
    SENSOR_CLASS = SensorState
    CONNECTION_FAILURES_LIMIT = 10

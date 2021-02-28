import logging
import struct
import uuid
from dataclasses import dataclass

from .base import SENSOR_DOMAIN, Sensor, SubscribeAndSetDataMixin

logger = logging.getLogger(__name__)

MAIN_DATA = uuid.UUID('70BC767E-7A1A-4304-81ED-14B9AF54F7BD')


@dataclass
class SensorState:
    battery: int
    dose: float
    dose_rate: float
    temperature: int

    @classmethod
    def from_data(cls, sensor_data):
        flags, dose, dose_rate, pulses, battery, temp = \
            struct.unpack('<BffHbb', sensor_data)
        return cls(
            dose=round(dose, 4),
            dose_rate=round(dose_rate, 4),
            battery=battery,
            temperature=temp,
        )


class AtomFast(SubscribeAndSetDataMixin, Sensor):
    NAME = 'atomfast'
    DATA_CHAR = MAIN_DATA
    SENSOR_CLASS = SensorState
    CONNECTION_FAILURES_LIMIT = 10
    MANUFACTURER = 'Atom'

    @property
    def entities(self):
        return {
            SENSOR_DOMAIN: [
                {
                    'name': 'temperature',
                    'device_class': 'temperature',
                    'unit_of_measurement': '\u00b0C',
                },
                {
                    'name': 'dose',
                    'unit_of_measurement': 'mSv',
                    'icon': 'atom',
                },
                {
                    'name': 'dose_rate',
                    'unit_of_measurement': 'μSv/h',
                    'icon': 'atom',
                },
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                },
            ],
        }

    def filter_notifications(self, sender):
        return sender == 0x24

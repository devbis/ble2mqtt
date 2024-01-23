import logging
from dataclasses import dataclass

from bleak.backends.device import BLEDevice

from ..devices.base import SENSOR_DOMAIN, Sensor
from ..protocols.ruuvi import DataFormat5Decoder
from ..utils import cr2477_voltage_to_percent, format_binary

_LOGGER = logging.getLogger(__name__)


@dataclass
class SensorState:
    battery: int = 0
    temperature: float = 0
    humidity: float = 0
    pressure: float = 0
    movement_counter: int = 0


class RuuviTag(Sensor):
    NAME = 'ruuvitag'
    MANUFACTURER = 'Ruuvi'
    SENSOR_CLASS = SensorState
    SUPPORT_PASSIVE = True
    SUPPORT_ACTIVE = False
    # send data only if temperature or humidity is set
    REQUIRED_VALUES = ('temperature', 'humidity', 'pressure')

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
                    'name': 'humidity',
                    'device_class': 'humidity',
                    'unit_of_measurement': '%',
                },
                {
                    'name': 'pressure',
                    'device_class': 'atmospheric_pressure',
                    'unit_of_measurement': 'hPa',
                },
                {
                    'name': 'movement_counter',
                },
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                    'entity_category': 'diagnostic',
                },
            ],
        }

    def handle_advert(self, scanned_device: BLEDevice, adv_data):
        raw_data = adv_data.manufacturer_data[0x0499]

        data_format = raw_data[0]
        if data_format != 0x05:
            _LOGGER.debug("Data format not supported: %s", raw_data[0])
            return

        if raw_data:
            decoder = DataFormat5Decoder(bytes(raw_data))
            self._state = self.SENSOR_CLASS(
                temperature=decoder.temperature_celsius,
                humidity=decoder.humidity_percentage,
                pressure=decoder.pressure_hpa,
                movement_counter=decoder.movement_counter,
                battery=int(cr2477_voltage_to_percent(
                    decoder.battery_voltage_mv,
                ))
            )

            _LOGGER.debug(
                f'Advert received for {self}, {format_binary(raw_data)}, '
                f'current state: {self._state}',
            )


class RuuviTagPro2in1(RuuviTag):
    NAME = 'ruuvitag_pro_2in1'

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
                    'name': 'movement_counter',
                    'device_class': 'count',
                },
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                    'entity_category': 'diagnostic',
                },
            ],
        }


class RuuviTagPro3in1(RuuviTag):
    NAME = 'ruuvitag_pro_3in1'

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
                    'name': 'humidity',
                    'device_class': 'humidity',
                    'unit_of_measurement': '%',
                },
                {
                    'name': 'movement_counter',
                    'device_class': 'count',
                },
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                    'entity_category': 'diagnostic',
                },
            ],
        }

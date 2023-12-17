import logging
from dataclasses import dataclass

from bleak.backends.device import BLEDevice

from ..devices.base import Sensor, SENSOR_DOMAIN, SubscribeAndSetDataMixin
from ..protocols.govee import GoveeDecoder
from ..utils import format_binary

_LOGGER = logging.getLogger(__name__)


@dataclass
class SensorState:
    battery: int = 0
    temperature: float = 0
    humidity: float = 0


class GoveeTemperature(SubscribeAndSetDataMixin, Sensor):
    NAME = 'govee'
    SENSOR_CLASS = SensorState
    SUPPORT_PASSIVE = True
    SUPPORT_ACTIVE = False
    REQUIRED_VALUES = ('temperature', 'humidity')

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
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                    'entity_category': 'diagnostic',
                },
            ],
        }

    def handle_advert(self, scanned_device: BLEDevice, adv_data):
        raw_data = adv_data.manufacturer_data.get(0xec88)
        if not raw_data:
            _LOGGER.debug(f'Temperature data not found; got {repr(adv_data.manufacturer_data)}')
            return

        if len(raw_data) != 7:
            _LOGGER.debug(f'Unexpected raw data length {len(raw_data)} ({repr(raw_data)})')

        decoder = GoveeDecoder(bytes(raw_data))
        self._state = self.SENSOR_CLASS(
            temperature=decoder.temperature_celsius,
            humidity=decoder.humidity_percentage,
            battery=decoder.battery_percentage,
        )

        _LOGGER.debug(
            f'Advert received for {self}, {format_binary(raw_data)}, '
            f'current state: {self._state}',
        )

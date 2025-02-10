import logging

from bleak.backends.device import BLEDevice

from ..devices.base import HumidityTemperatureSensor
from ..protocols.govee import GoveeDecoder
from ..utils import format_binary

_LOGGER = logging.getLogger(__name__)

def valid_data_length(raw_data):
    valid_lengths = [6, 7]
    return len(raw_data) in valid_lengths


class GoveeTemperature(HumidityTemperatureSensor):
    NAME = 'govee_ht'
    MANUFACTURER = 'Govee'
    SUPPORT_PASSIVE = True
    SUPPORT_ACTIVE = False

    def handle_advert(self, scanned_device: BLEDevice, adv_data):
        raw_data = adv_data.manufacturer_data.get(0xec88)
        if not raw_data:
            _LOGGER.debug(
                'Temperature data not found; got '
                f'{repr(adv_data.manufacturer_data)}',
            )
            return

        if not valid_data_length(raw_data):
            _LOGGER.debug(
                'Unexpected raw data length '
                f'{len(raw_data)} ({repr(raw_data)})',
            )

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

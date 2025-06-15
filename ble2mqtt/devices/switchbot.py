import logging

from bleak import AdvertisementData
from bleak.backends.device import BLEDevice

from ..devices.base import HumidityTemperatureSensor
from ..utils import format_binary

_LOGGER = logging.getLogger(__name__)

# Decoding was inspired by github.com/Trundle/meter-reader, github.com/Ernst79/bleparser & github.com/sblibs/pySwitchbot
# https://github.com/Trundle/meter-reader/blob/448a61dea487303817e902e7991708927a530847/src/meterreader_models/src/lib.rs#L98C12-L98C21
# https://github.com/Ernst79/bleparser/blob/6845901e04e164376a3f023c7a4a1f65db651cc7/package/bleparser/switchbot.py#L13
# https://github.com/sblibs/pySwitchbot/blob/3014972af637957f9dc29b2c4829faaec73f8526/switchbot/adv_parser.py
class SwitchBotHumidityTemperature(HumidityTemperatureSensor):
    MAC_TYPE = 'random'
    NAME = 'switchbot_ht'
    MANUFACTURER = 'SwitchBot'
    SUPPORT_PASSIVE = True
    SUPPORT_ACTIVE = False

    def handle_advert(self, scanned_device: BLEDevice, adv_data: AdvertisementData):
        data = adv_data.service_data.get("0000fd3d-0000-1000-8000-00805f9b34fb")
        if data is None:
            data = adv_data.service_data.get("00000d00-0000-1000-8000-00805f9b34fb")
        if not data:
            _LOGGER.debug(f'Service data not found for {self} got: {repr(adv_data)}')
            return

        if len(data) != 6:
            _LOGGER.error(f'Unexpected data length for {self} got: {format_binary(data)}')
            return

        if data[0] not in [84, 105]: # aka [0x54, 0x69] aka ["Meter", "Meter Plus"]
            _LOGGER.error(f'Unsupported model for {self} got: {format_binary(data)}')
            return

        temperature = float(data[4] & 127) + float((data[3] & 15) / 10.0)
        if data[4] & 128 == 0:
            temperature = -1 * temperature

        humidity = data[5] & 127
        battery = data[2] & 127

        self._state = self.SENSOR_CLASS(
            temperature=temperature,
            humidity=humidity,
            battery=battery,
        )

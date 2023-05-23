import logging
from dataclasses import dataclass

from bleak.backends.device import BLEDevice

from ..utils import format_binary
from .uuids import ENVIRONMENTAL_SENSING
from .xiaomi_base import XiaomiHumidityTemperature

_LOGGER = logging.getLogger(__name__)


@dataclass
class SensorState:
    battery: int = 0
    temperature: float = 0
    humidity: float = 0
    sends_custom: bool = False  # support decimal value for humidity


class XiaomiHumidityTemperatureLYWSDATC(XiaomiHumidityTemperature):
    NAME = 'xiaomilywsd_atc'
    SENSOR_CLASS = SensorState
    SUPPORT_PASSIVE = True
    SUPPORT_ACTIVE = False

    def handle_advert(self, scanned_device: BLEDevice, adv_data):
        service_data = adv_data.service_data
        adv_data = service_data.get(str(ENVIRONMENTAL_SENSING))

        if adv_data:
            sends_custom = bool(self._state and self._state.sends_custom)
            if len(adv_data) == 15:
                # b'\xe6o\xb98\xc1\xa4\x95\t\xff\x08~\x0cd\xe0\x04'
                sends_custom = True
                adv_data = bytes(adv_data)
                self._state = self.SENSOR_CLASS(
                    temperature=int.from_bytes(
                        adv_data[6:8], byteorder='little', signed=True) / 100,
                    humidity=int.from_bytes(
                        adv_data[8:10], byteorder='little') / 100,
                    battery=adv_data[12],
                    sends_custom=sends_custom,
                )

            elif len(adv_data) == 13:
                if sends_custom:
                    # ignore low res humidity packets
                    return
                # [a4 c1 38 84 7e 97 01 26 15 50 0b 73 17]
                #  <----- mac -----> temp hum bat
                adv_data = bytes(adv_data)
                self._state = self.SENSOR_CLASS(
                    temperature=int.from_bytes(
                        adv_data[6:8], byteorder='big', signed=True) / 10,
                    humidity=adv_data[8],
                    battery=adv_data[9],
                    sends_custom=sends_custom,
                )
                _LOGGER.debug(
                    f'Advert received for {self}, {format_binary(adv_data)}, '
                    f'current state: {self._state}',
                )

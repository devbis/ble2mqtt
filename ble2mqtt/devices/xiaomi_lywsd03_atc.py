import logging
from dataclasses import dataclass

from bleak.backends.device import BLEDevice

from .uuids import ENVIRONMENTAL_SENSING
from .xiaomi_base import XiaomiHumidityTemperature

logger = logging.getLogger(__name__)


@dataclass
class SensorState:
    battery: int = 0
    temperature: float = 0
    humidity: float = 0


class XiaomiHumidityTemperatureLYWSDATC(XiaomiHumidityTemperature):
    NAME = 'xiaomilywsd_atc'
    SENSOR_CLASS = SensorState
    SUPPORT_PASSIVE = True
    SUPPORT_ACTIVE = False

    def handle_advert(self, scanned_device: BLEDevice, adv_data):
        service_data = adv_data.service_data
        adv_data = service_data.get(str(ENVIRONMENTAL_SENSING))

        def from_word(data):
            return int.from_bytes(data, byteorder='big', signed=True) / 10

        if adv_data:
            # [a4 c1 38 84 7e 97 01 26 15 50 0b 73 17]
            #  <----- mac -----> temp hum bat
            adv_data = bytes(adv_data)
            self._state = self.SENSOR_CLASS(
                temperature=from_word(adv_data[6:8]),
                humidity=adv_data[8],
                battery=adv_data[9],
            )
            data_formatted = ' '.join(format(x, '02x') for x in adv_data)
            logger.debug(
                f'Advert received for {self}, {data_formatted}, '
                f'current state: {self._state}',
            )

import asyncio as aio
import logging
import uuid
from dataclasses import dataclass

from bleak.backends.device import BLEDevice

from .base import Device
from .xiaomi_base import XiaomiHumidityTemperature

logger = logging.getLogger(__name__)

ADVERTISING = uuid.UUID('0000181a-0000-1000-8000-00805f9b34fb')


@dataclass
class SensorState:
    battery: int = 0
    temperature: float = 0
    humidity: float = 0


class XiaomiHumidityTemperatureLYWSDATC(XiaomiHumidityTemperature, Device):
    NAME = 'xiaomilywsd_atc'
    SENSOR_CLASS = SensorState
    SUPPORT_PASSIVE = True
    SUPPORT_ACTIVE = False

    def handle_advert(self, scanned_device: BLEDevice, adv_data):
        service_data = adv_data.service_data
        adv_data = service_data.get(str(ADVERTISING))

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

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        while True:
            if not self._state:
                await aio.sleep(5)
                continue
            logger.debug(f'Try publish {self._state}')
            if self._state and self._state.temperature and self._state.humidity:
                if not self.config_sent:
                    await send_config(self)
                await self._notify_state(publish_topic)
            await aio.sleep(self.CONNECTION_TIMEOUT)

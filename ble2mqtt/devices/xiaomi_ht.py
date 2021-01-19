import asyncio as aio
import logging
import uuid
from dataclasses import dataclass

from bleak.backends.device import BLEDevice

from .base import Device
from .xiaomi_base import XiaomiHumidityTemperature

logger = logging.getLogger(__name__)

MJHT_DATA = uuid.UUID('226caa55-6476-4566-7562-66734470666d')
MJHT_BATTERY = uuid.UUID('00002a19-0000-1000-8000-00805f9b34fb')
ADVERTISING = uuid.UUID('0000fe95-0000-1000-8000-00805f9b34fb')


@dataclass
class SensorState:
    battery: int = 0
    temperature: float = 0
    humidity: float = 0

    @classmethod
    def from_data(cls, sensor_data, battery_data):
        # b'T=23.6 H=39.6\x00'
        t, h = tuple(
            float(x.split('=')[1])
            for x in sensor_data.decode().strip('\0').split(' ')
        )
        battery = int(ord(battery_data)) if battery_data else 0
        return cls(
            temperature=t,
            humidity=h,
            battery=battery,
        )


class XiaomiHumidityTemperatureV1(XiaomiHumidityTemperature, Device):
    NAME = 'xiaomihtv1'
    DATA_CHAR = MJHT_DATA
    BATTERY_CHAR = MJHT_BATTERY
    SENSOR_CLASS = SensorState
    SUPPORT_PASSIVE = True

    def filter_notifications(self, sender):
        # sender is 0xd or several requests it becomes
        # /org/bluez/hci0/dev_58_2D_34_32_E0_69/service000c/char000d
        return (
            sender == 0xd or
            isinstance(sender, str) and sender.endswith('000d')
        )

    def handle_advert(self, scanned_device: BLEDevice, adv_data):
        service_data = adv_data.service_data
        adv_data = service_data.get(str(ADVERTISING))

        def from_word(data):
            return int.from_bytes(data, byteorder='little', signed=True) / 10

        if adv_data:
            #                 <----- mac -----> typ  len <-- data -->
            # [50 20 aa 01 e4 69 e0 32 34 2d 58 0d 10 04 df 00 55 01]
            # [50 20 aa 01 80 69 e0 32 34 2d 58 0d 10 04 d6 00 29 01]
            adv_data = bytes(adv_data)
            typ = adv_data[11]
            if self._state is None:
                self._state = self.SENSOR_CLASS()
            if typ == 0x04:  # temperature
                self._state.temperature = from_word(adv_data[14:16])
            if typ == 0x06:  # humidity
                self._state.humidity = from_word(adv_data[14:16])
            if typ == 0x0a:  # battery
                self._state.battery = adv_data[14]
            if typ == 0x0d:  # humidity + temperature
                self._state.temperature = from_word(adv_data[14:16])
                self._state.humidity = from_word(adv_data[16:18])

            data_formatted = ' '.join(format(x, '02x') for x in adv_data)
            logger.debug(
                f'Advert received for {self}, {data_formatted}, '
                f'current state: {self._state}',
            )

    async def handle_passive(self, publish_topic, send_config):
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

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        if self.passive:
            return await self.handle_passive(
                publish_topic,
                send_config,
            )
        return await super().handle(publish_topic, send_config, *args, **kwargs)

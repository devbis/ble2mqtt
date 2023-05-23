import logging
import typing as ty
import uuid
from dataclasses import dataclass

from bleak.backends.device import BLEDevice

from ..protocols.xiaomi import parse_fe95_advert
from ..utils import format_binary
from .base import ConnectionMode
from .uuids import BATTERY
from .xiaomi_base import XiaomiHumidityTemperature

_LOGGER = logging.getLogger(__name__)

MJHT_DATA = uuid.UUID('226caa55-6476-4566-7562-66734470666d')
ADVERTISING = uuid.UUID('0000fe95-0000-1000-8000-00805f9b34fb')


@dataclass
class SensorState:
    battery: int = 0
    temperature: float = 0
    humidity: float = 0
    voltage: ty.Optional[float] = None

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


class XiaomiHumidityTemperatureV1(XiaomiHumidityTemperature):
    NAME = 'xiaomihtv1'
    DATA_CHAR = MJHT_DATA
    BATTERY_CHAR = BATTERY
    SENSOR_CLASS = SensorState
    SUPPORT_PASSIVE = True
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_POLL_WITH_DISCONNECT

    def filter_notifications(self, sender, data):
        # sender is 0xd or several requests it becomes
        # /org/bluez/hci0/dev_58_2D_34_32_E0_69/service000c/char000d
        return (
            sender == 0xd or
            isinstance(sender, str) and sender.endswith('000d')
        )

    def handle_advert(self, scanned_device: BLEDevice, adv_data):
        service_data = adv_data.service_data
        adv_data = service_data.get(str(ADVERTISING))

        if adv_data:
            # frctrl devic id <----- mac -----> type len <-- data -->
            # [50 20 aa 01 e4 69 e0 32 34 2d 58 0d 10 04 df 00 55 01]
            # [50 20 aa 01 80 69 e0 32 34 2d 58 0d 10 04 d6 00 29 01]

            try:
                parsed_advert = parse_fe95_advert(bytes(adv_data))
            except (ValueError, IndexError):
                _LOGGER.exception(f'{self} Cannot parse advert packet')
                return

            if self._state is None:
                self._state = self.SENSOR_CLASS()
            for k, v in parsed_advert.items():
                setattr(self._state, k, v)

            _LOGGER.debug(
                f'Advert received for {self}, {format_binary(adv_data)}, '
                f'current state: {self._state}',
            )

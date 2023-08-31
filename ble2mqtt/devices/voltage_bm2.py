from dataclasses import dataclass

from ..devices.base import (SENSOR_DOMAIN, ConnectionMode, Sensor,
                            SubscribeAndSetDataMixin)

UUID_KEY_READ = "0000fff4-0000-1000-8000-00805f9b34fb"
KEY = b"\x6c\x65\x61\x67\x65\x6e\x64\xff\xfe\x31\x38\x38\x32\x34\x36\x36"


def create_aes():
    try:
        from Crypto.Cipher import AES
    except ImportError:
        raise ImportError(
            "Please install pycryptodome to setup BM2 Voltage meter",
        ) from None

    return AES.new(KEY, AES.MODE_CBC, bytes([0] * 16))


@dataclass
class SensorState:
    voltage: float

    @classmethod
    def from_data(cls, decrypted_data: bytes):
        voltage = (int.from_bytes(
            decrypted_data[1:1 + 2],
            byteorder='big',
        ) >> 4) / 100
        return cls(voltage=round(voltage, 2))


class VoltageTesterBM2(SubscribeAndSetDataMixin, Sensor):
    NAME = 'voltage_bm2'
    DATA_CHAR = UUID_KEY_READ
    MANUFACTURER = 'BM2'
    SENSOR_CLASS = SensorState
    REQUIRED_VALUES = ('voltage', )
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_POLL_WITH_DISCONNECT

    @property
    def entities(self):
        return {
            SENSOR_DOMAIN: [
                {
                    'name': 'voltage',
                    'device_class': 'voltage',
                    'unit_of_measurement': 'V',
                },
            ],
        }

    def process_data(self, data: bytearray):
        decrypted_data = create_aes().decrypt(data)
        if decrypted_data[0] == 0xf5:
            self._state = self.SENSOR_CLASS.from_data(decrypted_data)

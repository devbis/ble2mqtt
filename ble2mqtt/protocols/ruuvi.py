"""
Decoder for RuuviTag Data Format 5 data.
Based on https://github.com/Bluetooth-Devices/ruuvitag-ble/blob/0e99249/src/ruuvitag_ble/df5_decoder.py (MIT Licensed)  # noqa
Which was based on https://github.com/ttu/ruuvitag-sensor/blob/23e6555/ruuvitag_sensor/decoder.py (MIT Licensed)  # noqa
"""
from __future__ import annotations

import math
import struct
import typing as ty


class DataFormat5Decoder:
    def __init__(self, raw_data: bytes) -> None:
        if len(raw_data) < 24:
            raise ValueError(
                "Data must be at least 24 bytes long for data format 5",
            )
        self.data = struct.unpack(">BhHHhhhHBH6B", raw_data)

    @property
    def temperature_celsius(self) -> ty.Optional[float]:
        if self.data[1] == -32768:
            return None
        return round(self.data[1] / 200.0, 2)

    @property
    def humidity_percentage(self) -> ty.Optional[float]:
        if self.data[2] == 65535:
            return None
        return round(self.data[2] / 400, 2)

    @property
    def pressure_hpa(self) -> ty.Optional[float]:
        if self.data[3] == 0xFFFF:
            return None

        return round((self.data[3] + 50000) / 100, 2)

    @property
    def acceleration_vector_mg(self) \
            -> ty.Union[
                ty.Sequence[int, int, int],
                ty.Sequence[None, None, None],
            ]:
        ax = self.data[4]
        ay = self.data[5]
        az = self.data[6]
        if ax == -32768 or ay == -32768 or az == -32768:
            return None, None, None

        return ax, ay, az

    @property
    def acceleration_total_mg(self) -> ty.Optional[float]:
        ax, ay, az = self.acceleration_vector_mg
        if ax is None or ay is None or az is None:
            return None
        return math.sqrt(ax * ax + ay * ay + az * az)

    @property
    def battery_voltage_mv(self) -> ty.Optional[int]:
        voltage = self.data[7] >> 5
        if voltage == 0b11111111111:
            return None

        return voltage + 1600

    @property
    def tx_power_dbm(self) -> ty.Optional[int]:
        tx_power = self.data[7] & 0x001F
        if tx_power == 0b11111:
            return None

        return -40 + (tx_power * 2)

    @property
    def movement_counter(self) -> int:
        return self.data[8]

    @property
    def measurement_sequence_number(self) -> int:
        return self.data[9]

    @property
    def mac(self) -> str:
        return ":".join(f"{x:02X}" for x in self.data[10:])

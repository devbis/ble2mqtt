"""
Decoder for Govee temperature/humidity sensors.
Based on https://github.com/wcbonner/GoveeBTTempLogger/blob/master/goveebttemplogger.cpp (MIT License)  # noqa: E501
"""
from __future__ import annotations

import struct
import typing as ty


class GoveeDecoder:
    def __init__(self, raw_data: bytes) -> None:
        # note: currently only H5074 is supported
        if len(raw_data) != 7:
            raise ValueError("Govee data must be 7 bytes long")
        self.data = struct.unpack("<xhhBx", raw_data)

    @property
    def temperature_celsius(self) -> ty.Optional[float]:
        if self.data[0] == -32768:
            return None
        return round(self.data[0] / 100.0, 2)

    @property
    def humidity_percentage(self) -> ty.Optional[float]:
        if self.data[0] == -32768:
            return None
        return round(self.data[1] / 100.0, 2)

    @property
    def battery_percentage(self) -> int:
        return self.data[2]

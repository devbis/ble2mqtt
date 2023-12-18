"""
Decoder for Govee temperature/humidity sensors.
Based on https://github.com/wcbonner/GoveeBTTempLogger/blob/master/goveebttemplogger.cpp (MIT License)
"""
from __future__ import annotations

import math
import struct


class GoveeDecoder:
    def __init__(self, raw_data: bytes) -> None:
        # note: currently only H5074 is supported
        if len(raw_data) != 7:
            raise ValueError("Govee data must be 7 bytes long")
        self.data: tuple[int, ...] = struct.unpack("<xhhBx", raw_data)

    @property
    def temperature_celsius(self) -> float | None:
        if self.data[0] == -32768:
            return None
        return round(self.data[0] / 100.0, 2)

    @property
    def humidity_percentage(self) -> float | None:
        if self.data[0] == -32768:
            return None
        return round(self.data[1] / 100.0, 2)

    @property
    def battery_percentage(self) -> int | None:
        return self.data[2]

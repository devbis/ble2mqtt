"""
Decoder for Govee temperature/humidity sensors.
Based on https://github.com/wcbonner/GoveeBTTempLogger/blob/master/goveebttemplogger.cpp (MIT License)  # noqa: E501
"""
from __future__ import annotations
from enum import Enum

import struct
import typing as ty

class PartNumber(Enum):
    H5074 = 0
    H5075 = 1


def get_intermediate_temp_h5075(unpacked_bytes):
    """
    Decode the data and separate out the sign from the temperature. Note that
    the humidity and temperature are both encoded into the 24 bit returned
    value.
    """
    raw = int.from_bytes(unpacked_bytes, byteorder="big")
    is_negative = (raw & 0x800000) > 0
    temp = raw & 0x7FFFF

    return (temp, is_negative)


class GoveeDecoder:
    def __init__(self, raw_data: bytes) -> None:
        if len(raw_data) == 7:
            self.data = struct.unpack("<xhhBx", raw_data)
            self.part_number = PartNumber.H5074

        elif len(raw_data) == 6:
            self.data = struct.unpack("<x3sBx", raw_data)
            self.part_number = PartNumber.H5075

        else:
            raise ValueError("Govee data must be 6 or 7 bytes long")

    @property
    def temperature_celsius(self) -> ty.Optional[float]:
        if self.part_number == PartNumber.H5074:
            if self.data[0] == -32768:
                return None
            return round(self.data[0] / 100.0, 2)

        elif self.part_number == PartNumber.H5075:
            temp, is_negative = get_intermediate_temp_h5075(self.data[0])

            # This is temp/1000/10
            #see: https://github.com/wcbonner/GoveeBTTempLogger/issues/49
            temp /= 10000.0

            if is_negative:
                temp *= -1.0

            return temp

        else:
            return None

    @property
    def humidity_percentage(self) -> ty.Optional[float]:
        if self.part_number == PartNumber.H5074:
            if self.data[0] == -32768:
                return None
            return round(self.data[1] / 100.0, 2)

        elif self.part_number == PartNumber.H5075:
            temp, _ = get_intermediate_temp_h5075(self.data[0])
            humidity = (temp % 1000.0) / 10.0
            return humidity

        else:
            return None

    @property
    def battery_percentage(self) -> int:
        if self.part_number == PartNumber.H5074:
            return self.data[2]

        elif self.part_number == PartNumber.H5075:
            return self.data[1]

        else:
            return None

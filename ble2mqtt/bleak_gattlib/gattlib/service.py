from typing import List

from bleak.backends.service import BleakGATTService

from .characteristic import BleakGATTCharacteristicGattlib


class BleakGATTServiceGattlib(BleakGATTService):
    """GATT Characteristic implementation for the Gattlib backend"""

    def __init__(self, obj: dict):
        super().__init__(obj)
        self.__characteristics = []

    @property
    def handle(self) -> int:
        """The integer handle of this service"""
        return self.obj['start']

    @property
    def uuid(self) -> str:
        """UUID for this service."""
        return self.obj['uuid']

    @property
    def characteristics(self) -> List[BleakGATTCharacteristicGattlib]:
        """List of characteristics for this service"""
        return self.__characteristics

    def add_characteristic(self, characteristic: BleakGATTCharacteristicGattlib):
        """Add a :py:class:`~BleakGATTCharacteristicGattlib` to the service.

        Should not be used by end user, but rather by `bleak` itself.
        """
        self.__characteristics.append(characteristic)

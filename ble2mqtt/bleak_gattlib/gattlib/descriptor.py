"""
Interface class for the Bleak representation of a GATT Descriptor
"""

from bleak.backends.descriptor import BleakGATTDescriptor


class BleakGATTDescriptorGattlib(BleakGATTDescriptor):
    """GATT Descriptor implementation for Gattlib backend"""

    def __init__(
        self, obj, characteristic_uuid: str, characteristic_handle: int,
    ):
        super(BleakGATTDescriptorGattlib, self).__init__(obj)
        self.obj = obj
        self.__characteristic_uuid = characteristic_uuid
        self.__characteristic_handle = characteristic_handle

    @property
    def characteristic_handle(self) -> int:
        """handle for the characteristic that this descriptor belongs to"""
        return self.__characteristic_handle

    @property
    def characteristic_uuid(self) -> str:
        """UUID for the characteristic that this descriptor belongs to"""
        return self.__characteristic_uuid

    @property
    def uuid(self) -> str:
        """UUID for this descriptor"""
        return self.obj['uuid']

    @property
    def handle(self) -> int:
        """Integer handle for this descriptor"""
        return self.obj['handle']

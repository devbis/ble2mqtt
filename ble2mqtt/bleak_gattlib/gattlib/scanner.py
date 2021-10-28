import asyncio
import logging
import typing as ty
import uuid
from typing import List

from bleak import BleakError
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData, BaseBleakScanner

from gattlib import DiscoveryService, ScanEntryType

logger = logging.getLogger(__name__)


def get_manufacturer_data(data) -> dict:
    if not data:
        return {}
    manufacturer_handle = int.from_bytes(
        data[:2],
        byteorder='little',
    )
    return {manufacturer_handle: data[2:]}


class BleakScannerGattlib(BaseBleakScanner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # kwarg "device" is for backwards compatibility
        self._adapter = kwargs.get("adapter", kwargs.get("device", "hci0"))
        self.service = None
        self.scanner_task = None

        self._devices: ty.Dict[str, dict] = {}

    async def process_scan(self):
        while True:
            self.service.do_step(50)
            await asyncio.sleep(0.05)

    def discover_callback(self, address, data):
        address = address.upper()
        self._devices[address] = data

        logger.debug(f'scan data: {address} {data}')

        _local_name = data['name']
        _rssi = data['rssi']
        _manufacturer_data = data['info'].get(ScanEntryType.MANUFACTURER)
        _service_data = {}
        for k, v in data['info'].items():
            if k not in [
                ScanEntryType.SERVICE_DATA_16B,
                ScanEntryType.SERVICE_DATA_32B,
                ScanEntryType.SERVICE_DATA_128B,
            ]:
                continue
            # 0x16 Service Data - 16-bit UUID
            # 0x20 Service Data - 32-bit UUID
            # 0x21 Service Data - 128-bit UUID
            if k == ScanEntryType.SERVICE_DATA_16B:
                k = uuid.UUID(
                    '%08x-0000-1000-8000-00805f9b34fb' %
                    int.from_bytes(v[:2], 'little'),
                )
                v = v[2:]
            elif k == ScanEntryType.SERVICE_DATA_32B:
                k = uuid.UUID(
                    '%08x-0000-1000-8000-00805f9b34fb' %
                    int.from_bytes(v[:4], 'little'),
                )
                v = v[4:]
            elif k == ScanEntryType.SERVICE_DATA_128B:
                k = uuid.UUID(bytes_le=v[:8])
                v = v[8:]
            _service_data[str(k)] = v
        logger.debug(f'_service_data: {_service_data}')

        advertisement_data = AdvertisementData(
            local_name=_local_name,
            manufacturer_data=get_manufacturer_data(_manufacturer_data),
            service_data=_service_data,
            service_uuids=list(_service_data.keys()),
            platform_data=None,
        )

        device = BLEDevice(
            address,
            _local_name or address.upper().replace(':', '-'),
            data,
            _rssi,
        )
        self._callback(device, advertisement_data)

    async def start(self):
        try:
            self.service = DiscoveryService(self._adapter)
        except RuntimeError as e:
            raise BleakError(str(e)) from None
        self.service.set_callback(self.discover_callback)

        try:
            self.service.start()
        except RuntimeError:
            try:
                self.service.stop()
                await asyncio.sleep(0)
                self.service.start()
            except RuntimeError as e:
                raise BleakError(str(e))

        self.scanner_task = asyncio.ensure_future(self.process_scan())

    async def stop(self):
        if self.service:
            self.service.stop()
        if not self.scanner_task.done():
            self.scanner_task.cancel()
        try:
            await self.scanner_task
        except asyncio.CancelledError:
            pass

        self.service = None

    async def set_scanning_filter(self, **kwargs):
        raise NotImplementedError('Not implemented for gattlib backend')

    @property
    def discovered_devices(self) -> List[BLEDevice]:
        # Reduce output.
        discovered_devices = []
        for address, data in self._devices.items():
            address = address.upper()
            name = data['name']
            rssi = data['rssi']

            uuids = []
            for k, v in data['info'].items():
                if k not in [
                    ScanEntryType.SERVICE_DATA_16B,
                    ScanEntryType.SERVICE_DATA_32B,
                    ScanEntryType.SERVICE_DATA_128B,
                ]:
                    continue
                # 0x16 Service Data - 16-bit UUID
                # 0x20 Service Data - 32-bit UUID
                # 0x21 Service Data - 128-bit UUID
                if k == ScanEntryType.SERVICE_DATA_16B:
                    uuids.append(str(uuid.UUID(
                        '%08x-0000-1000-8000-00805f9b34fb' %
                        int.from_bytes(v[:2], 'little'),
                    )))
                elif k == ScanEntryType.SERVICE_DATA_32B:
                    uuids.append(str(uuid.UUID(
                        '%08x-0000-1000-8000-00805f9b34fb' %
                        int.from_bytes(v[:4], 'little'),
                    )))
                elif k == ScanEntryType.SERVICE_DATA_128B:
                    uuids.append(str(uuid.UUID(bytes_le=v[:8])))
            discovered_devices.append(
                BLEDevice(
                    address,
                    name or address.replace(':', '-'),
                    {"scan_entry": data['info']},
                    rssi,
                    uuids=uuids,
                    manufacturer_data=get_manufacturer_data(
                        data['info'].get(ScanEntryType.MANUFACTURER),
                    ),
                ),
            )
        return discovered_devices

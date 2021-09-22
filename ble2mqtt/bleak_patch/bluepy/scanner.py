import asyncio
import binascii
import concurrent.futures
import logging
import typing as ty
import uuid
from typing import List

from bluepy.btle import (BTLEDisconnectError, BTLEException, BTLEGattError,
                         BTLEInternalError, BTLEManagementError,
                         DefaultDelegate, ScanEntry, Scanner)

from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData, BaseBleakScanner

logger = logging.getLogger(__name__)


def get_manufacturer_data(data) -> dict:
    if not data:
        return {}
    manufacturer_handle = int.from_bytes(
        data[:2],
        byteorder='little',
    )
    return {manufacturer_handle: data[2:]}


class ScanDelegate(DefaultDelegate):
    def __init__(self, bleak_scanner):
        DefaultDelegate.__init__(self)
        self.bleak_scanner = bleak_scanner

    async def handle_discovery_async(self, scan_entry, is_new_dev, is_new_data):
        if is_new_dev:
            logger.debug("Discovered device %s", scan_entry.addr)

        elif is_new_data:
            logger.debug("Received new data from %s", scan_entry.addr)
        scan_entry.addr = scan_entry.addr.upper()
        self.bleak_scanner.push_discovered_device(scan_entry)

        logger.debug(f'Scan entry: {scan_entry.__dict__}')
        logger.debug(f'Scan data: {scan_entry.scanData}')

        _local_name = scan_entry.getValue(ScanEntry.SHORT_LOCAL_NAME) or \
            scan_entry.getValue(ScanEntry.COMPLETE_LOCAL_NAME)
        _manufacturer_data = scan_entry.getValue(ScanEntry.MANUFACTURER)
        # _service_data = scan_entry.scanData
        _service_data = {}
        for k, v in scan_entry.scanData.items():
            if k not in [
                ScanEntry.SERVICE_DATA_16B,
                ScanEntry.SERVICE_DATA_32B,
                ScanEntry.SERVICE_DATA_128B,
            ]:
                continue
            # 0x16 Service Data - 16-bit UUID
            # 0x20 Service Data - 32-bit UUID
            # 0x21 Service Data - 128-bit UUID
            if k == ScanEntry.SERVICE_DATA_16B:
                k = uuid.UUID(
                    '%08x-0000-1000-8000-00805f9b34fb' %
                    int.from_bytes(v[:2], 'little'),
                )
                v = v[2:]
            elif k == ScanEntry.SERVICE_DATA_32B:
                k = uuid.UUID(
                    '%08x-0000-1000-8000-00805f9b34fb' %
                    int.from_bytes(v[:4], 'little'),
                )
                v = v[4:]
            elif k == ScanEntry.SERVICE_DATA_128B:
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
        # print(scan_entry.getValueText(8))
        # print(scan_entry.getValueText(9))
        # # print(scan_entry.getDescription(8))
        # print(scan_entry.__dict__)

        device = BLEDevice(
            scan_entry.addr,
            _local_name or scan_entry.addr.upper().replace(':', '-'),
            scan_entry.scanData,
            scan_entry.rssi,
        )
        self.bleak_scanner._callback(device, advertisement_data)


class QueueScanner(Scanner):

    def __init__(self, iface=0):
        Scanner.__init__(self, iface)
        self.queue = asyncio.Queue()

    def _wait_resp_queue(self, wantType, timeout=None):
        while True:
            if self._helper is None:
                raise BTLEInternalError("Scan stopped")
            if self._helper.poll() is not None:
                raise BTLEInternalError("Helper exited")

            if timeout:
                fds = self._poller.poll(timeout * 1000)
                if len(fds) == 0:
                    # DBG("Select timeout")
                    self.queue.put_nowait(None)
                    return None

            rv = self._helper.stdout.readline()
            # DBG("Got:", repr(rv))
            if rv.startswith('#') or rv == '\n' or len(rv) == 0:
                continue

            resp = self.parseResp(rv)
            if 'rsp' not in resp:
                raise BTLEInternalError("No response type indicator", resp)

            respType = resp['rsp'][0]
            if respType in wantType:
                self.queue.put_nowait(resp)
                # return resp
                return
            elif respType == 'stat':
                if 'state' in resp and len(resp['state']) > 0 and \
                        resp['state'][0] == 'disc':
                    self._stopHelper()
                    self.queue.put_nowait(
                        BTLEDisconnectError("Device disconnected", resp))
                    raise BTLEDisconnectError("Device disconnected", resp)
            elif respType == 'err':
                errcode = resp['code'][0]
                if errcode == 'nomgmt':
                    self.queue.put_nowait(BTLEManagementError(
                        "Management not available (permissions problem?)",
                        resp))
                    raise BTLEManagementError(
                        "Management not available (permissions problem?)", resp)
                elif errcode == 'atterr':
                    self.queue.put_nowait(BTLEGattError("Bluetooth command failed", resp))
                    raise BTLEGattError("Bluetooth command failed", resp)
                else:
                    self.queue.put_nowait(BTLEException(
                        "Error from bluepy-helper (%s)" % errcode, resp))
                    raise BTLEException(
                        "Error from bluepy-helper (%s)" % errcode, resp)
            elif respType == 'scan':
                # Scan response when we weren't interested. Ignore it
                continue
            else:
                self.queue.put_nowait(BTLEInternalError(
                    "Unexpected response (%s)" % respType, resp,
                ))
                raise BTLEInternalError("Unexpected response (%s)" % respType,
                                        resp)

    async def process_async(self):
        if self._helper is None:
            raise BTLEInternalError(
                "Helper not started (did you call start()?)")
        while True:
            await asyncio.sleep(0)
            loop = asyncio.get_event_loop()
            with concurrent.futures.ThreadPoolExecutor() as pool:
                loop.run_in_executor(
                    pool,
                    self._wait_resp_queue,
                    ['scan', 'stat'],
                    10,  # timeout
                )
            resp = await self.queue.get()
            if resp is None:
                break

            if isinstance(resp, Exception):
                raise resp

            respType = resp['rsp'][0]
            if respType == 'stat':
                # if scan ended, restart it
                if resp['state'][0] == 'disc':
                    self._mgmtCmd(self._cmd())

            elif respType == 'scan':
                # device found
                addr = binascii.b2a_hex(resp['addr'][0]).decode('utf-8')
                addr = ':'.join([addr[i:i + 2] for i in range(0, 12, 2)])
                if addr in self.scanned:
                    dev = self.scanned[addr]
                else:
                    dev = ScanEntry(addr, self.iface)
                    self.scanned[addr] = dev
                is_new_data = dev._update(resp)
                if self.delegate is not None:
                    await self.delegate.handle_discovery_async(
                        dev, (dev.updateCount <= 1), is_new_data,
                    )
            else:
                raise BTLEInternalError(
                    "Unexpected response: " + respType, resp,
                )


class BleakScannerBluePy(BaseBleakScanner):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)

        # kwarg "device" is for backwards compatibility
        self._adapter = kwargs.get("adapter", kwargs.get("device", "hci0"))
        self.service = None
        self.scanner_task = None

        self._devices: ty.Dict[str, ScanEntry] = {}

    async def start(self):
        loop = asyncio.get_event_loop()
        self.service = QueueScanner(int(self._adapter[3:])).withDelegate(
            ScanDelegate(self),
        )
        self.service.start()
        self.scanner_task = loop.create_task(self.service.process_async())

    async def stop(self):
        try:
            self.service.stop()
        except BTLEDisconnectError:
            pass
        if not self.scanner_task.done():
            self.scanner_task.cancel()
            try:
                await self.scanner_task
            except asyncio.CancelledError:
                pass

        self.service = None

    async def set_scanning_filter(self, **kwargs):
        raise NotImplementedError('Not implemented for bluepy backend')

    def push_discovered_device(self, device):
        self._devices[device.addr] = device

    @property
    def discovered_devices(self) -> List[BLEDevice]:
        # Reduce output.
        discovered_devices = []
        for path, scan_entry in self._devices.items():
            address = scan_entry.addr.upper()
            name = scan_entry.getValue(ScanEntry.SHORT_LOCAL_NAME) or \
                scan_entry.getValue(ScanEntry.COMPLETE_LOCAL_NAME)
            rssi = scan_entry.rssi

            uuids = []
            for k, v in scan_entry.scanData.items():
                if k not in [
                    ScanEntry.SERVICE_DATA_16B,
                    ScanEntry.SERVICE_DATA_32B,
                    ScanEntry.SERVICE_DATA_128B,
                ]:
                    continue
                # 0x16 Service Data - 16-bit UUID
                # 0x20 Service Data - 32-bit UUID
                # 0x21 Service Data - 128-bit UUID
                if k == ScanEntry.SERVICE_DATA_16B:
                    uuids.append(str(uuid.UUID(
                        '%08x-0000-1000-8000-00805f9b34fb' %
                        int.from_bytes(v[:2], 'little'),
                    )))
                elif k == ScanEntry.SERVICE_DATA_32B:
                    uuids.append(str(uuid.UUID(
                        '%08x-0000-1000-8000-00805f9b34fb' %
                        int.from_bytes(v[:4], 'little'),
                    )))
                elif k == ScanEntry.SERVICE_DATA_128B:
                    uuids.append(str(uuid.UUID(bytes_le=v[:8])))
            discovered_devices.append(
                BLEDevice(
                    address,
                    name or address.replace(':', '-'),
                    {"path": path, "scan_entry": scan_entry},
                    rssi,
                    uuids=uuids,
                    manufacturer_data=get_manufacturer_data(scan_entry.rawData),
                ),
            )
        return discovered_devices

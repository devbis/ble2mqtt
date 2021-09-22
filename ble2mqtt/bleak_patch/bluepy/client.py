import asyncio
import binascii
import logging
import os
import re
import struct
import uuid
from functools import partial
from typing import Callable, Optional, Union

from bluepy.btle import (AssignedNumbers, BTLEException, BTLEInternalError,
                         DefaultDelegate, Peripheral, helperExe,
                         BTLEManagementError, BTLEDisconnectError,
                         BTLEGattError, ScanEntry, ADDR_TYPE_PUBLIC,
                         ADDR_TYPE_RANDOM, Service, Characteristic, UUID)

from bleak import BleakError
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.client import BaseBleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.service import BleakGATTServiceCollection

logger = logging.getLogger(__name__)


class AsyncCharacteristic(Characteristic):
    async def getDescriptors(self, forUUID=None, hndEnd=0xFFFF):
        if not self.descs:
            # Descriptors (not counting the value descriptor) begin after
            # the handle for the value descriptor and stop when we reach
            # the handle for the next characteristic or service
            self.descs = []
            for desc in await self.peripheral.getDescriptors(self.valHandle+1, hndEnd):
                if desc.uuid in (0x2800, 0x2801, 0x2803):
                    # Stop if we reach another characteristic or service
                    break
                self.descs.append(desc)
        if forUUID is not None:
            u = UUID(forUUID)
            return [desc for desc in self.descs if desc.uuid == u]
        return self.descs


class Descriptor:
    def __init__(self, *args):
        (self.peripheral, uuidVal, self.handle) = args
        self.uuid = UUID(uuidVal)

    def __str__(self):
        return "Descriptor <%s>" % self.uuid.getCommonName()

    async def read(self):
        return await self.peripheral.readCharacteristic(self.handle)

    async def write(self, val, withResponse=False):
        await self.peripheral.writeCharacteristic(self.handle, val, withResponse)


class SubprocessProtocol(asyncio.SubprocessProtocol):
    def __init__(self, stdout_queue: asyncio.Queue, exit_future):
        self.exit_future = exit_future
        self.output = bytearray()
        self.stdout_queue: asyncio.Queue = stdout_queue

    def pipe_data_received(self, fd, data):
        if fd == 1:  # got stdout data (bytes)
            self.output.extend(data)
            if b'\n' in self.output:
                line, rest = self.output.split(b'\n', 1)
                self.stdout_queue.put_nowait(line)
                self.output = rest

    def process_exited(self):
        self.exit_future.set_result(True)

    def connection_lost(self, exc):
        print("Connection lost")
        # loop.stop() # end loop.run_forever()


class AsyncBluepyHelper:
    def __init__(self):
        self._helper = None
        self._lineq = None
        # self._stderr = None
        self._mtu = 0
        self.delegate = DefaultDelegate()
        self.task = None

    async def helper_task(self):
        pass

    def withDelegate(self, delegate_):
        self.delegate = delegate_
        return self

    async def _startHelper(self,iface=None):
        if self._helper is None:
            # logger.debug("Running ", helperExe)
            self._lineq = asyncio.Queue()
            self._mtu = 0
            # self._stderr = open(os.devnull, "w")
            args=[helperExe]
            if iface is not None: args.append(str(iface))

            # loop = asyncio.get_running_loop()
            # exit_future = asyncio.Future(loop=loop)
            # transport, protocol = await loop.subprocess_exec(
            #     lambda: SubprocessProtocol(self._lineq, exit_future),
            #     *args,
            #     # stdin=None,
            #     stderr=None,
            # )
            #
            # # Wait for the subprocess exit using the process_exited()
            # # method of the protocol.
            # await exit_future
            #
            # # Close the stdout pipe.
            # transport.close()

            self._helper = await asyncio.subprocess.create_subprocess_exec(
                helperExe,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                # universal_newlines=True,
                # preexec_fn = preexec_function,
            )
            self.task = asyncio.create_task(self._readToQueue())

            # t = Thread(target=self._readToQueue)
            # t.daemon = True               # don't wait for it to exit
            # t.start()

    async def _readToQueue(self):
        """Thread to read lines from stdout and insert in queue."""
        buf = bytearray()
        while True:
            # stdout, stderr = await self._helper.communicate()
            # print(stdout, stderr)
            c = await self._helper.stdout.read(1)
            if c == b'\n' and buf:
                print('stdout', buf)
                self._lineq.put_nowait(buf.decode())
                buf.clear()
            else:
                buf.extend(c)
        #
        #     if b'\n' in self.output:
        #         line, rest = self.output.split(b'\n', 1)
        #
        # # while await self._helper.poll():
        # #     line = self._helper.stdout.readline()
        #     if not stdout:                  # EOF
        #         break
        #     if stdout:
        #         await self._lineq.put(stdout.split('\n'))

    async def _stopHelper(self):
        if self._helper is not None:
            # logger.debug("Stopping ", helperExe)
            # stdout, stdin = await self._helper.communicate(input=b"quit\n")
            # print(stdout, stdin)
            # if stdout:
            #     # for t in re.split(r'[\x1e\n]', stdout.decode()):
            #     for t in stdout.decode().split('\n'):
            #         print(f'<-- {t}')
            #         await self._lineq.put(t)
            self._helper.stdin.write("quit\n")
            await self._helper.stdin.drain()
            # self._helper.stdin.flush()
            await self._helper.wait()
            self._helper = None
        # if self._stderr is not None:
        #     self._stderr.close()
        #     self._stderr = None

    async def _writeCmd(self, cmd):
        if self._helper is None:
            raise BTLEInternalError("Helper not started (did you call connect()?)")
        # logger.debug("Sent: ", cmd)
        print(f'--> {cmd.encode()}')
        self._helper.stdin.write(f"{cmd}\n".encode())
        await self._helper.stdin.drain()
        #
        # stdout, stderr = await self._helper.communicate(input=cmd.encode())
        # print(stdout, stderr)
        # if stdout:
        #     # for t in re.split(r'[\x1e\n]', stdout.decode()):
        #     for t in stdout.decode().split('\n'):
        #         print(f'<-- {t}')
        #         await self._lineq.put(t)
        # self._helper.stdin.write(cmd)
        # self._helper.stdin.flush()

    async def _mgmtCmd(self, cmd):
        await self._writeCmd(cmd + '\n')
        rsp = await self._waitResp('mgmt')
        if rsp['code'][0] != 'success':
            await self._stopHelper()
            raise BTLEManagementError(
                "Failed to execute management command '%s'" % (cmd),
                rsp,
            )

    @staticmethod
    def parseResp(line):
        resp = {}
        for item in line.rstrip().split('\x1e'):
            (tag, tval) = item.split('=')
            if len(tval) == 0:
                val = None
            elif tval[0] == "$" or tval[0] == "'":
                # Both symbols and strings as Python strings
                val = tval[1:]
            elif tval[0] == "h":
                val = int(tval[1:], 16)
            elif tval[0] == 'b':
                val = binascii.a2b_hex(tval[1:].encode('utf-8'))
            else:
                raise BTLEInternalError(
                    "Cannot understand response value %s" % repr(tval),
                )
            if tag not in resp:
                resp[tag] = [val]
            else:
                resp[tag].append(val)
        return resp

    async def _waitResp(self, wantType, timeout=None):
        while True:
            # if self._helper.returncode is not None:
            #     raise BTLEInternalError(f"Helper exited with {self._helper.returncode}")

            try:
                rv = await asyncio.wait_for(self._lineq.get(), timeout=timeout)
            except asyncio.TimeoutError:
                logger.info("Select timeout")
                return None

            logger.info("Got:" + repr(rv))
            if rv.startswith('#') or rv == '\n' or len(rv) == 0:
                continue

            resp = self.parseResp(rv)
            if 'rsp' not in resp:
                raise BTLEInternalError("No response type indicator", resp)

            respType = resp['rsp'][0]

            # always check for MTU updates
            if 'mtu' in resp and len(resp['mtu']) > 0:
                new_mtu = int(resp['mtu'][0])
                if self._mtu != new_mtu:
                    self._mtu = new_mtu
                    logger.debug("Updated MTU: " + str(self._mtu))

            if respType in wantType:
                return resp
            elif respType == 'stat':
                if 'state' in resp and len(resp['state']) > 0 and resp['state'][0] == 'disc':
                    await self._stopHelper()
                    raise BTLEDisconnectError("Device disconnected", resp)
            elif respType == 'err':
                errcode = resp['code'][0]
                if errcode == 'nomgmt':
                    raise BTLEManagementError(
                        "Management not available (permissions problem?)", resp)
                elif errcode == 'atterr':
                    raise BTLEGattError("Bluetooth command failed", resp)
                else:
                    raise BTLEException("Error from bluepy-helper (%s)" % errcode, resp)
            elif respType == 'scan':
                # Scan response when we weren't interested. Ignore it
                continue
            else:
                raise BTLEInternalError("Unexpected response (%s)" % respType, resp)

    async def status(self):
        await self._writeCmd("stat\n")
        return await self._waitResp(['stat'])


class AsyncPeripheral(AsyncBluepyHelper):
    def __init__(self, deviceAddr=None, addrType='public', iface=None, timeout=None):
        super().__init__()
        self._serviceMap = None  # Indexed by UUID
        (self.deviceAddr, self.addrType, self.iface) = (None, None, None)

        if isinstance(deviceAddr, ScanEntry):
            self._connect(deviceAddr.addr, deviceAddr.addrType, deviceAddr.iface, timeout)
        elif deviceAddr is not None:
            self._connect(deviceAddr, addrType, iface, timeout)

    def setDelegate(self, delegate_): # same as withDelegate(), deprecated
        return self.withDelegate(delegate_)

    def __aenter__(self):
        return self

    async def __aexit__(self, type, value, traceback):
        await self.disconnect()

    async def _getResp(self, wantType, timeout=None):
        if isinstance(wantType, list) is not True:
            wantType = [wantType]

        while True:
            resp = await self._waitResp(wantType + ['ntfy', 'ind'], timeout)
            if resp is None:
                return None

            respType = resp['rsp'][0]
            if respType == 'ntfy' or respType == 'ind':
                hnd = resp['hnd'][0]
                data = resp['d'][0]
                if self.delegate is not None:
                    self.delegate.handleNotification(hnd, data)
            if respType not in wantType:
                continue
            return resp

    async def _connect(self, addr, addrType='public', iface=None, timeout=None):
        if len(addr.split(":")) != 6:
            raise ValueError("Expected MAC address, got %s" % repr(addr))
        if addrType not in (ADDR_TYPE_PUBLIC, ADDR_TYPE_RANDOM):
            raise ValueError("Expected address type public or random, got {}".format(addrType))
        await self._startHelper(iface)
        self.addr = addr
        self.addrType = addrType
        self.iface = iface
        if iface is not None:
            await self._writeCmd("conn %s %s %s\n" % (addr, addrType, "hci"+str(iface)))
        else:
            await self._writeCmd("conn %s %s\n" % (addr, addrType))
        rsp = await self._getResp('stat', timeout)
        print('!', rsp)
        timeout_exception = BTLEDisconnectError(
            "Timed out while trying to connect to peripheral %s, addr type: %s" %
            (addr, addrType), rsp)
        if rsp is None:
            raise timeout_exception
        print('rsp', rsp)
        while rsp and rsp['state'][0] == 'tryconn':
            rsp = await self._getResp('stat', timeout)
            print('rsp', rsp)
        if rsp is None or rsp['state'][0] != 'conn':
            await self._stopHelper()
            if rsp is None:
                raise timeout_exception
            else:
                raise BTLEDisconnectError("Failed to connect to peripheral %s, addr type: %s"
                                          % (addr, addrType), rsp)

    async def connect(self, addr, addrType='public', iface=None, timeout=None):
        if isinstance(addr, ScanEntry):
            await self._connect(addr.addr, addr.addrType, addr.iface, timeout)
        elif addr is not None:
            await self._connect(addr, addrType, iface, timeout)

    async def disconnect(self):
        if self._helper is None:
            return
        # Unregister the delegate first
        self.setDelegate(None)

        await self._writeCmd("disc\n")
        await self._getResp('stat')
        await self._stopHelper()

    async def discoverServices(self):
        await self._writeCmd("svcs\n")
        rsp = await self._getResp('find')
        starts = rsp['hstart']
        ends = rsp['hend']
        uuids = rsp['uuid']
        nSvcs = len(uuids)
        assert (len(starts) == nSvcs and len(ends) == nSvcs)
        self._serviceMap = {}
        for i in range(nSvcs):
            self._serviceMap[UUID(uuids[i])] = Service(self, uuids[i], starts[i], ends[i])
        return self._serviceMap

    async def getState(self):
        status = await self.status()
        return status['state'][0]

    async def services(self):
        if self._serviceMap is None:
            self._serviceMap = await self.discoverServices()
        return self._serviceMap.values()

    async def getServices(self):
        return await self.services()

    async def getServiceByUUID(self, uuidVal):
        uuid = UUID(uuidVal)
        if self._serviceMap is not None and uuid in self._serviceMap:
            return self._serviceMap[uuid]
        await self._writeCmd("svcs %s\n" % uuid)
        rsp = await self._getResp('find')
        if 'hstart' not in rsp:
            raise BTLEGattError("Service %s not found" % (uuid.getCommonName()), rsp)
        svc = Service(self, uuid, rsp['hstart'][0], rsp['hend'][0])

        if self._serviceMap is None:
            self._serviceMap = {}
        self._serviceMap[uuid] = svc
        return svc

    async def _getIncludedServices(self, startHnd=1, endHnd=0xFFFF):
        # TODO: No working example of this yet
        await self._writeCmd("incl %X %X\n" % (startHnd, endHnd))
        return await self._getResp('find')

    async def getCharacteristics(self, startHnd=1, endHnd=0xFFFF, uuid=None):
        cmd = 'char %X %X' % (startHnd, endHnd)
        if uuid:
            cmd += ' %s' % UUID(uuid)
        await self._writeCmd(cmd + "\n")
        rsp = await self._getResp('find')
        nChars = len(rsp['hnd'])
        return [AsyncCharacteristic(self, rsp['uuid'][i], rsp['hnd'][i],
                               rsp['props'][i], rsp['vhnd'][i])
                for i in range(nChars)]

    async def getDescriptors(self, startHnd=1, endHnd=0xFFFF):
        await self._writeCmd("desc %X %X\n" % (startHnd, endHnd))
        # Historical note:
        # Certain Bluetooth LE devices are not capable of sending back all
        # descriptors in one packet due to the limited size of MTU. So the
        # guest needs to check the response and make retries until all handles
        # are returned.
        # In bluez 5.25 and later, gatt_discover_desc() in attrib/gatt.c does the retry
        # so bluetooth_helper always returns a full list.
        # This was broken in earlier versions.
        resp = await self._getResp('desc')
        ndesc = len(resp['hnd'])
        return [Descriptor(self, resp['uuid'][i], resp['hnd'][i]) for i in range(ndesc)]

    async def readCharacteristic(self, handle):
        await self._writeCmd("rd %X\n" % handle)
        resp = await self._getResp('rd')
        return resp['d'][0]

    async def _readCharacteristicByUUID(self, uuid, startHnd, endHnd):
        # Not used at present
        await self._writeCmd("rdu %s %X %X\n" % (UUID(uuid), startHnd, endHnd))
        return await self._getResp('rd')

    async def writeCharacteristic(self, handle, val, withResponse=False, timeout=None):
        # Without response, a value too long for one packet will be truncated,
        # but with response, it will be sent as a queued write
        cmd = "wrr" if withResponse else "wr"
        await self._writeCmd("%s %X %s\n" % (cmd, handle, binascii.b2a_hex(val).decode('utf-8')))
        return await self._getResp('wr', timeout)

    async def setSecurityLevel(self, level):
        await self._writeCmd("secu %s\n" % level)
        return await self._getResp('stat')

    async def unpair(self):
        await self._mgmtCmd("unpair")

    async def pair(self):
        await self._mgmtCmd("pair")

    def getMTU(self):
        return self._mtu

    async def setMTU(self, mtu):
        await self._writeCmd("mtu %x\n" % mtu)
        return await self._getResp('stat')

    async def waitForNotifications(self, timeout):
        resp = await self._getResp(['ntfy','ind'], timeout)
        return resp is not None

    async def _setRemoteOOB(self, address, address_type, oob_data, iface=None):
        if self._helper is None:
            await self._startHelper(iface)
        self.addr = address
        self.addrType = address_type
        self.iface = iface
        cmd = "remote_oob " + address + " " + address_type
        if oob_data['C_192'] is not None and oob_data['R_192'] is not None:
            cmd += " C_192 " + oob_data['C_192'] + " R_192 " + oob_data['R_192']
        if oob_data['C_256'] is not None and oob_data['R_256'] is not None:
            cmd += " C_256 " + oob_data['C_256'] + " R_256 " + oob_data['R_256']
        if iface is not None:
            cmd += " hci" + str(iface)
        await self._writeCmd(cmd)

    async def setRemoteOOB(self, address, address_type, oob_data, iface=None):
        if len(address.split(":")) != 6:
            raise ValueError("Expected MAC address, got %s" % repr(address))
        if address_type not in (ADDR_TYPE_PUBLIC, ADDR_TYPE_RANDOM):
            raise ValueError("Expected address type public or random, got {}".format(address_type))
        if isinstance(address, ScanEntry):
            return await self._setRemoteOOB(address.addr, address.addrType, oob_data, address.iface)
        elif address is not None:
            return self._setRemoteOOB(address, address_type, oob_data, iface)

    async def getLocalOOB(self, iface=None):
        if self._helper is None:
            await self._startHelper(iface)
        self.iface = iface
        await self._writeCmd("local_oob\n")
        resp = await self._getResp('oob')
        if resp is not None:
            data = resp.get('d', [''])[0]
            if data is None:
                raise BTLEManagementError(
                    "Failed to get local OOB data.")
            if struct.unpack_from('<B', data, 0)[0] != 8 or \
                struct.unpack_from('<B', data, 1)[0] != 0x1b:
                raise BTLEManagementError(
                    "Malformed local OOB data (address).")
            address = data[2:8]
            address_type = data[8:9]
            if struct.unpack_from('<B', data, 9)[0] != 2 or \
                struct.unpack_from('<B', data, 10)[0] != 0x1c:
                raise BTLEManagementError(
                    "Malformed local OOB data (role).")
            role = data[11:12]
            if struct.unpack_from('<B', data, 12)[0] != 17 or \
                struct.unpack_from('<B', data, 13)[0] != 0x22:
                raise BTLEManagementError(
                    "Malformed local OOB data (confirm).")
            confirm = data[14:30]
            if struct.unpack_from('<B', data, 30)[0] != 17 or \
                struct.unpack_from('<B', data, 31)[0] != 0x23:
                raise BTLEManagementError(
                    "Malformed local OOB data (random).")
            random = data[32:48]
            if struct.unpack_from('<B', data, 48)[0] != 2 or \
                struct.unpack_from('<B', data, 49)[0] != 0x1:
                raise BTLEManagementError(
                    "Malformed local OOB data (flags).")
            flags = data[50:51]
            return {'Address': ''.join(
                ["%02X" % struct.unpack('<B', c)[0] for c in address]),
                    'Type': ''.join(["%02X" % struct.unpack('<B', c)[0] for c in
                                     address_type]),
                    'Role': ''.join(
                        ["%02X" % struct.unpack('<B', c)[0] for c in role]),
                    'C_256': ''.join(
                        ["%02X" % struct.unpack('<B', c)[0] for c in confirm]),
                    'R_256': ''.join(
                        ["%02X" % struct.unpack('<B', c)[0] for c in random]),
                    'Flags': ''.join(
                        ["%02X" % struct.unpack('<B', c)[0] for c in flags]),
                    }

    # def __del__(self):
    #     self.disconnect()


class ClientDelegate(DefaultDelegate):
        def __init__(self, bleak_scanner):
            DefaultDelegate.__init__(self)
            self.bleak_scanner = bleak_scanner

        async def handle_discovery_async(self, scan_entry, is_new_dev,
                                         is_new_data):
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
            print(scan_entry.getValueText(8))
            print(scan_entry.getValueText(9))
            # print(scan_entry.getDescription(8))
            print(scan_entry.__dict__)

            device = BLEDevice(
                scan_entry.addr,
                _local_name or scan_entry.addr.upper().replace(':', '-'),
                scan_entry.scanData,
                scan_entry.rssi,
            )
            self.bleak_scanner._callback(device, advertisement_data)


#
# class QueuePeripheral(Peripheral):
#     # add async code
#
#     async def connect(self, address, type, timeout):
#         loop = asyncio.get_running_loop()
#         try:
#             result = await loop.run_in_executor(
#                 None, partial(self._connect, address, type, timeout=timeout))
#         except BTLEException as e:
#             raise BleakError() from e
#         print('default thread pool', result)
#
#     async def disconnect(self):
#         loop = asyncio.get_running_loop()
#         try:
#             result = await loop.run_in_executor(
#                 None, super().disconnect)
#         except BTLEException as e:
#             raise BleakError() from e
#
#     def _start_notify(self, uuid):
#         ch = self.getCharacteristics(uuid=uuid)[0]
#         desc = ch.getDescriptors(forUUID=AssignedNumbers.client_characteristic_configuration)[0]
#         desc.write(0x01.to_bytes(2, byteorder="little"), withResponse=True)
#
#     async def start_notify(self, uuid):
#         loop = asyncio.get_running_loop()
#         await loop.run_in_executor(
#                         None, partial(self._start_notify, uuid))


class BleakClientBluePy(BaseBleakClient):

    def __init__(self, address_or_ble_device: Union[BLEDevice, str], **kwargs):
        super().__init__(address_or_ble_device, **kwargs)
        self._address_type = (
            kwargs["address_type"]
            if "address_type" in kwargs
            and kwargs["address_type"] in ("public", "random")
            else 'public'
        )

        self.peripheral: Optional[AsyncPeripheral] = None

    async def start_notify(
            self,
            char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
            callback: Callable[[int, bytearray], None],
            **kwargs) -> None:
        if not self.peripheral:
            raise BleakError('Not connected')

        ach = (await self.peripheral.getCharacteristics(uuid=char_specifier))[0]
        desc = (await ach.getDescriptors(forUUID=AssignedNumbers.client_characteristic_configuration))[0]
        await self.peripheral.writeCharacteristic(
            desc.handle,
            0x01.to_bytes(2, byteorder="little"),
            withResponse=True,
        )

    async def stop_notify(self, char_specifier: Union[
            BleakGATTCharacteristic, int, str, uuid.UUID]) -> None:
        if not self.peripheral:
            raise BleakError('Not connected')

        ach = (await self.peripheral.getCharacteristics(uuid=char_specifier))[0]
        desc = (await ach.getDescriptors(forUUID=AssignedNumbers.client_characteristic_configuration))[0]
        await self.peripheral.writeCharacteristic(
            desc.handle,
            0x00.to_bytes(2, byteorder="little"),
            withResponse=True,
        )

    async def write_gatt_descriptor(self, handle: int, data: bytearray) -> None:
        pass

    async def write_gatt_char(
            self,
            char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
            data: bytearray,
            response: bool = False) -> None:
        ach = (await self.peripheral.getCharacteristics(uuid=char_specifier))[0]
        return await self.peripheral.writeCharacteristic(
            ach.valHandle,
            data,
            withResponse=response,
        )

    async def read_gatt_descriptor(self, handle: int, **kwargs) -> bytearray:
        pass

    async def read_gatt_char(
            self,
            char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
            **kwargs) -> bytearray:
        ach = (await self.peripheral.getCharacteristics(uuid=char_specifier))[0]
        return await self.peripheral.readCharacteristic(ach.valHandle)

    async def get_services(self, **kwargs) -> BleakGATTServiceCollection:
        pass

    async def unpair(self) -> bool:
        pass

    async def pair(self, *args, **kwargs) -> bool:
        pass

    @property
    def is_connected(self) -> bool:
        if not self.peripheral:
            return False
        return True
        # state = None
        # async def _is_connected():
        #     global state
        #     state = await self.peripheral.getState() == 'conn'
        #
        # try:
        #     loop = asyncio.get_running_loop()
        #     loop.en(_is_connected)
        #     return state
        # except BTLEInternalError:
        #     return False

    async def connect(self, **kwargs) -> bool:
        logger.debug(
            f"Connecting to device @ {self.address}")

        if self.is_connected:
            raise BleakError("Client is already connected")

        # A Discover must have been run before connecting to any devices.
        # Find the desired device before trying to connect.
        timeout = kwargs.get("timeout", self._timeout)

        self.peripheral = AsyncPeripheral(None)
        try:
            await self.peripheral.connect(
                self.address,
                self._address_type,
                timeout=timeout,
            )
        except BTLEException as e:
            raise BleakError(str(e)) from e
        return True

    async def disconnect(self) -> bool:
        if self.peripheral:
            try:
                await self.peripheral.disconnect()
            except BTLEException as e:
                logger.exception("Error in disconnect")
                # raise BleakError() from e
        return True

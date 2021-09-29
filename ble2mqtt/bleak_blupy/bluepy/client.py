import asyncio
import binascii
import logging
import struct
import uuid
from typing import Callable, Optional, Union

from bleak import BleakError
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.client import BaseBleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.service import BleakGATTServiceCollection
from bluepy.btle import (ADDR_TYPE_PUBLIC, ADDR_TYPE_RANDOM, UUID,
                         AssignedNumbers, BTLEDisconnectError, BTLEException,
                         BTLEGattError, BTLEInternalError, BTLEManagementError,
                         Characteristic, DefaultDelegate, ScanEntry, Service,
                         helperExe)

logger = logging.getLogger(__name__)


class AsyncCharacteristic(Characteristic):
    async def getDescriptors(self, forUUID=None, hndEnd=0xFFFF):
        if not self.descs:
            # Descriptors (not counting the value descriptor) begin after
            # the handle for the value descriptor and stop when we reach
            # the handle for the next characteristic or service
            self.descs = []
            for desc in await self.peripheral.getDescriptors(self.valHandle + 1,
                                                             hndEnd):
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
        await self.peripheral.writeCharacteristic(self.handle, val,
                                                  withResponse)


# class SubprocessProtocol(asyncio.SubprocessProtocol):
#     def __init__(self, stdout_queue: asyncio.Queue, exit_future):
#         self.exit_future = exit_future
#         self.output = bytearray()
#         self.stdout_queue: asyncio.Queue = stdout_queue
#
#     def pipe_data_received(self, fd, data):
#         if fd == 1:  # got stdout data (bytes)
#             self.output.extend(data)
#             if b'\n' in self.output:
#                 line, rest = self.output.split(b'\n', 1)
#                 self.stdout_queue.put_nowait(line)
#                 self.output = rest
#
#     def process_exited(self):
#         self.exit_future.set_result(True)
#
#     def connection_lost(self, exc):
#         print("Connection lost")
#         # loop.stop() # end loop.run_forever()


class ClientNotificationDelegate(DefaultDelegate):
    def __init__(self, bleak_client):
        DefaultDelegate.__init__(self)
        self.bleak_client = bleak_client

    def handleNotification(self, handle, data):
        self.bleak_client.push_notification(handle, data)


class AsyncBluepyHelper:
    def __init__(self):
        self._helper = None
        self._lineq = None
        self._mtu = 0
        self.delegate = DefaultDelegate()
        self.last_state = None
        self.reader_task = None

    async def helper_task(self):
        pass

    def withDelegate(self, delegate_):
        self.delegate = delegate_
        return self

    async def _startHelper(self, iface=None):
        if self._helper is None:
            logger.debug(f"Running {helperExe}")
            self._lineq = asyncio.Queue()
            self._responseq = asyncio.Queue()
            self._mtu = 0
            args = [helperExe]
            if iface is not None:
                args.append(str(iface))

            self._helper = await asyncio.subprocess.create_subprocess_exec(
                helperExe,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            self.reader_task = asyncio.create_task(self._readToQueue())
            self.parser_task = asyncio.create_task(self._parse_queue_messages())

    async def _readToQueue(self):
        """Thread to read lines from stdout and insert in queue."""
        output = bytearray()
        while self._helper:  # and self._helper.stdout.at_eof():
            c = await self._helper.stdout.read(1)
            if c == b'\n' and output:
                self._lineq.put_nowait(output.decode())
                output.clear()
                continue
            output.extend(c)

    async def _parse_queue_messages(self, timeout=None):
        while True:
            if self._helper and self._helper.returncode is not None:
                return
                # raise BTLEInternalError(
                #     f"Helper exited with {self._helper.returncode}")

            rv = await self._lineq.get()
            logger.debug("Got:" + repr(rv))
            if rv.startswith('#') or rv == '\n' or len(rv) == 0:
                continue

            resp = self.parseResp(rv)
            if 'rsp' not in resp:
                raise BTLEInternalError("No response type indicator", resp)

            respType = resp['rsp'][0]
            if respType == 'ntfy' or respType == 'ind':
                hnd = resp['hnd'][0]
                data = resp['d'][0]

                if self.delegate is not None:
                    self.delegate.handleNotification(hnd, data)
            else:
                if respType == 'stat':
                    if 'state' in resp and len(resp['state']) > 0:
                        self.last_state = resp['state'][0]
                        if self.last_state == 'disc':
                            await self._stopHelper()
                        # raise BTLEDisconnectError("Device disconnected", resp)
                await self._responseq.put(resp)

    async def _stopHelper(self):
        if self._helper is not None:
            logger.debug(f"Stopping {helperExe}")
            await asyncio.sleep(1)
            try:
                self.reader_task.cancel()
                await self.reader_task
            except asyncio.CancelledError:
                pass
            self.reader_task = None

            # logger.debug(f"Stopping {helperExe}")
            # logger.debug("Sent: {}".format(b'quit\n'))
            # self._helper.stdin.write(b"quit\n")
            # await self._helper.stdin.drain()
            # logger.debug(f"Wait for quit {helperExe}")
            # await self._helper.communicate(input="quit\n".encode())
            await self._helper.terminate()
            await asyncio.wait_for(self._helper.wait(), timeout=5)
            self._helper = None
            self.last_state = 'disc'

            try:
                self.parser_task.cancel()
                self.parser_task.result()
            except asyncio.CancelledError:
                pass
            self.parser_task = None

    async def _writeCmd(self, cmd):
        if self._helper is None:
            raise BTLEInternalError(
                "Helper not started (did you call connect()?)")
        logger.debug(f"Sent: {cmd}")
        self._helper.stdin.write(f"{cmd}\n".encode())
        await self._helper.stdin.drain()

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
            if self._helper.returncode is not None:
                raise BTLEInternalError(
                    f"Helper exited with {self._helper.returncode}")

            resp = await self._responseq.get()
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
                if (
                        'state' in resp and
                        len(resp['state']) > 0 and
                        resp['state'][0] == 'disc'
                ):
                    await self._stopHelper()
                    raise BTLEDisconnectError("Device disconnected", resp)
            elif respType == 'err':
                errcode = resp['code'][0]
                if errcode == 'nomgmt':
                    raise BTLEManagementError(
                        "Management not available (permissions problem?)",
                        resp,
                    )
                elif errcode == 'atterr':
                    raise BTLEGattError("Bluetooth command failed", resp)
                else:
                    raise BTLEException(
                        "Error from bluepy-helper (%s)" % errcode,
                        resp,
                    )
            elif respType == 'scan':
                # Scan response when we weren't interested. Ignore it
                continue
            else:
                raise BTLEInternalError(
                    "Unexpected response (%s)" % respType,
                    resp,
                )

    async def status(self):
        await self._writeCmd("stat\n")
        return await self._waitResp(['stat'])


class AsyncPeripheral(AsyncBluepyHelper):
    def __init__(self, deviceAddr=None, addrType='public', iface=None,
                 timeout=None):
        super().__init__()
        self._serviceMap = None  # Indexed by UUID
        (self.deviceAddr, self.addrType, self.iface) = (None, None, None)

        if isinstance(deviceAddr, ScanEntry):
            self._connect(deviceAddr.addr, deviceAddr.addrType,
                          deviceAddr.iface, timeout)
        elif deviceAddr is not None:
            self._connect(deviceAddr, addrType, iface, timeout)

    def setDelegate(self, delegate_):  # same as withDelegate(), deprecated
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
            if respType not in wantType:
                continue
            return resp

    async def _connect(self, addr, addrType='public', iface=None, timeout=None):
        if len(addr.split(":")) != 6:
            raise ValueError("Expected MAC address, got %s" % repr(addr))
        if addrType not in (ADDR_TYPE_PUBLIC, ADDR_TYPE_RANDOM):
            raise ValueError(
                "Expected address type public or random, got {}".format(
                    addrType,
                ),
            )
        await self._startHelper(iface)
        self.addr = addr
        self.addrType = addrType
        self.iface = iface
        if iface is not None:
            await self._writeCmd(
                "conn %s %s %s\n" % (addr, addrType, "hci" + str(iface)),
            )
        else:
            await self._writeCmd("conn %s %s\n" % (addr, addrType))
        rsp = await self._getResp('stat', timeout)
        timeout_exception = BTLEDisconnectError(
            "Timed out while trying to connect to peripheral %s, "
            "addr type: %s" %
            (addr, addrType), rsp)
        if rsp is None:
            raise timeout_exception
        while rsp and rsp['state'][0] == 'tryconn':
            rsp = await self._getResp('stat', timeout)
        if rsp is None or rsp['state'][0] != 'conn':
            await self._stopHelper()
            if rsp is None:
                raise timeout_exception
            else:
                raise BTLEDisconnectError(
                    "Failed to connect to peripheral %s, addr type: %s"
                    % (addr, addrType),
                    rsp,
                )

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
            self._serviceMap[UUID(uuids[i])] = \
                Service(self, uuids[i], starts[i], ends[i])
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
            raise BTLEGattError(
                "Service %s not found" % (uuid.getCommonName()),
                rsp,
            )
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
        return [
            AsyncCharacteristic(
                self,
                rsp['uuid'][i],
                rsp['hnd'][i],
                rsp['props'][i],
                rsp['vhnd'][i],
            )
            for i in range(nChars)
        ]

    async def getDescriptors(self, startHnd=1, endHnd=0xFFFF):
        await self._writeCmd("desc %X %X\n" % (startHnd, endHnd))
        # Historical note:
        # Certain Bluetooth LE devices are not capable of sending back all
        # descriptors in one packet due to the limited size of MTU. So the
        # guest needs to check the response and make retries until all handles
        # are returned.
        # In bluez 5.25 and later, gatt_discover_desc() in attrib/gatt.c does
        # the retry so bluetooth_helper always returns a full list.
        # This was broken in earlier versions.
        resp = await self._getResp('desc')
        ndesc = len(resp['hnd'])
        return [Descriptor(self, resp['uuid'][i], resp['hnd'][i]) for i in
                range(ndesc)]

    async def readCharacteristic(self, handle):
        await self._writeCmd("rd %X\n" % handle)
        resp = await self._getResp('rd')
        return resp['d'][0]

    async def _readCharacteristicByUUID(self, uuid, startHnd, endHnd):
        # Not used at present
        await self._writeCmd("rdu %s %X %X\n" % (UUID(uuid), startHnd, endHnd))
        return await self._getResp('rd')

    async def writeCharacteristic(self, handle, val, withResponse=False,
                                  timeout=None):
        # Without response, a value too long for one packet will be truncated,
        # but with response, it will be sent as a queued write
        cmd = "wrr" if withResponse else "wr"
        await self._writeCmd("%s %X %s\n" % (
            cmd,
            handle,
            binascii.b2a_hex(val).decode('utf-8'),
        ))
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
        resp = await self._getResp(['ntfy', 'ind'], timeout)
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
            raise ValueError(
                "Expected address type public or random, got {}".format(
                    address_type,
                ),
            )
        if isinstance(address, ScanEntry):
            return await self._setRemoteOOB(address.addr, address.addrType,
                                            oob_data, address.iface)
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


class BleakClientBluePy(BaseBleakClient):

    def __init__(self, address_or_ble_device: Union[BLEDevice, str], **kwargs):
        super().__init__(address_or_ble_device, **kwargs)
        self._address_type = (
            kwargs["address_type"]
            if "address_type" in kwargs
            and kwargs["address_type"] in ("public", "random")
            else 'public'
        )
        self._notification_callbacks = {}

        self.peripheral: Optional[AsyncPeripheral] = None

    async def start_notify(
            self,
            char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
            callback: Callable[[int, bytearray], None],
            **kwargs) -> None:
        if not self.peripheral:
            raise BleakError('Not connected')

        ach: Characteristic = (
            await self.peripheral.getCharacteristics(uuid=char_specifier)
        )[0]
        self._notification_callbacks[ach.valHandle] = callback

        desc: Descriptor = (await ach.getDescriptors(
            forUUID=AssignedNumbers.client_characteristic_configuration))[0]
        self.peripheral.setDelegate(ClientNotificationDelegate(self))
        await self.peripheral.writeCharacteristic(
            desc.handle,
            0x01.to_bytes(2, byteorder="little"),
            withResponse=True,
        )

    async def stop_notify(
        self,
        char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
    ) -> None:
        if not self.peripheral:
            raise BleakError('Not connected')

        ach = (await self.peripheral.getCharacteristics(uuid=char_specifier))[0]
        desc = (await ach.getDescriptors(
            forUUID=AssignedNumbers.client_characteristic_configuration))[0]
        await self.peripheral.writeCharacteristic(
            desc.handle,
            0x00.to_bytes(2, byteorder="little"),
            withResponse=True,
        )
        self._notification_callbacks.pop(ach.valHandle, None)

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
        res = await self.peripheral.readCharacteristic(ach.valHandle)
        print('    ------>    read_gatt_char', char_specifier, res)
        return res

    async def get_services(self, **kwargs) -> BleakGATTServiceCollection:
        pass

    async def unpair(self) -> bool:
        pass

    async def pair(self, *args, **kwargs) -> bool:
        pass

    def push_notification(self, handle, data):
        logger.debug(f'notification from {handle}: {data}')
        callback = self._notification_callbacks.get(handle)
        if callback:
            callback(int(handle) - 1, data)

    @property
    def is_connected(self) -> bool:
        if not self.peripheral:
            return False
        return self.peripheral.last_state == 'conn'

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
                logger.exception(f"Error in disconnect: {e}")
                # raise BleakError() from e
        return True

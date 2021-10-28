import asyncio
import enum
import inspect
import logging
import os
import uuid
from typing import Callable, Optional, Union, List

from bleak import BleakError
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.client import BaseBleakClient
from bleak.backends.device import BLEDevice
from bleak.backends.service import BleakGATTServiceCollection

import gattlib
from gattlib import GATTRequester, GATTResponse, BTIOException

from .characteristic import BleakGATTCharacteristicGattlib
# from .descriptor import BleakGATTDescriptorGattlib
from .service import BleakGATTServiceGattlib
from .uuids import DescriptorUUID

logger = logging.getLogger(__name__)


class BleakGattlibResponseError(BleakError):
    def __init__(self, text, err_code) -> None:
        # allows to accept err_code as kwarg
        super().__init__(text, err_code)

    @property
    def err_code(self) -> Optional[int]:
        if len(self.args) > 1:
            return self.args[1]
        return None


class BleakGattlibIOError(BleakError):
    pass


class BleakConnectionError(BleakError):
    pass


class GattlibErrors(enum.Enum):
    ATT_ECODE_INVALID_HANDLE = gattlib.ATT_ECODE_INVALID_HANDLE
    ATT_ECODE_READ_NOT_PERM = gattlib.ATT_ECODE_READ_NOT_PERM
    ATT_ECODE_WRITE_NOT_PERM = gattlib.ATT_ECODE_WRITE_NOT_PERM
    ATT_ECODE_INVALID_PDU = gattlib.ATT_ECODE_INVALID_PDU
    ATT_ECODE_AUTHENTICATION = gattlib.ATT_ECODE_AUTHENTICATION
    ATT_ECODE_REQ_NOT_SUPP = gattlib.ATT_ECODE_REQ_NOT_SUPP
    ATT_ECODE_INVALID_OFFSET = gattlib.ATT_ECODE_INVALID_OFFSET
    ATT_ECODE_AUTHORIZATION = gattlib.ATT_ECODE_AUTHORIZATION
    ATT_ECODE_PREP_QUEUE_FULL = gattlib.ATT_ECODE_PREP_QUEUE_FULL
    ATT_ECODE_ATTR_NOT_FOUND = gattlib.ATT_ECODE_ATTR_NOT_FOUND
    ATT_ECODE_ATTR_NOT_LONG = gattlib.ATT_ECODE_ATTR_NOT_LONG
    ATT_ECODE_INSUFF_ENCR_KEY_SIZE = gattlib.ATT_ECODE_INSUFF_ENCR_KEY_SIZE
    ATT_ECODE_INVAL_ATTR_VALUE_LEN = gattlib.ATT_ECODE_INVAL_ATTR_VALUE_LEN
    ATT_ECODE_UNLIKELY = gattlib.ATT_ECODE_UNLIKELY
    ATT_ECODE_INSUFF_ENC = gattlib.ATT_ECODE_INSUFF_ENC
    ATT_ECODE_UNSUPP_GRP_TYPE = gattlib.ATT_ECODE_UNSUPP_GRP_TYPE
    ATT_ECODE_INSUFF_RESOURCES = gattlib.ATT_ECODE_INSUFF_RESOURCES
    ATT_ECODE_IO = gattlib.ATT_ECODE_IO
    ATT_ECODE_TIMEOUT = gattlib.ATT_ECODE_TIMEOUT
    ATT_ECODE_ABORTED = gattlib.ATT_ECODE_ABORTED


class Response(GATTResponse):
    # def __init__(self, client: BaseBleakClient):
    def __init__(self, loop):
        super().__init__()
        self.loop = loop
        self.future = asyncio.Future()
        self.data = []

    def on_response(self, data):
        # logger.debug(f'.. received {data}')
        self.data.append(data)

    def on_response_complete(self):
        self.loop.call_soon_threadsafe(self.future.set_result, self.data)

    def on_response_failed(self, status):
        self.loop.call_soon_threadsafe(
            self.future.set_exception,
            BleakGattlibResponseError(
                f'Error on processing command: status '
                f'{GattlibErrors(status).name}',
                err_code=GattlibErrors(status),
            ),
        )

    async def wait_receive(self):
        result = await self.future
        # logger.debug(f'data packet finished')
        return result


class Requester(GATTRequester):
    def __init__(self, client: 'BleakClientGattlib', loop, *args):
        GATTRequester.__init__(self, *args)
        self.client = client
        self.loop = loop
        self._connection_state_changed = asyncio.Event()
        self._error_code = None

    def on_connect(self, mtu):
        logger.debug(f'{self.client.address} connected')
        self.loop.call_soon_threadsafe(self._connection_state_changed.set)

    def on_connect_failed(self, err_code):
        logger.debug(f'{self.client.address} connect failed')
        self._error_code = err_code
        self.loop.call_soon_threadsafe(self._connection_state_changed.set)

    def on_disconnect(self):
        logger.info(f'{self.client.address} disconnected')
        self.loop.call_soon_threadsafe(self._connection_state_changed.set)

    def on_notification(self, handle, data):
        return self.client.push_notification(handle, data[3:])

    def on_indication(self, handle, data):
        return self.on_notification(handle, data)

    async def connect_(self, address_type, timeout=10.0):
        self.loop.call_soon_threadsafe(self._connection_state_changed.clear)
        self._error_code = None
        self.connect(
            False,  # wait
            address_type,  # channel_type
        )
        try:
            await asyncio.wait_for(
                self._connection_state_changed.wait(),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            raise BleakConnectionError(
                f'Timed out after {timeout} seconds',
            ) from None
        if self._error_code:
            raise BleakConnectionError(
                f"{os.strerror(self._error_code)} ({self._error_code})",
            )
        if not self.is_connected():
            raise BleakConnectionError("Connection failed")
        return True

    async def disconnect_(self):
        self.loop.call_soon_threadsafe(self._connection_state_changed.clear)
        self.disconnect()
        self.loop.call_soon_threadsafe(self._connection_state_changed.set)

    async def discover_services(self) -> List[dict]:
        response = Response(self.loop)
        logger.debug('call discover_primary_async()')
        self.discover_primary_async(response)
        return await response.wait_receive()

    async def discover_characteristics(self, service) -> List[dict]:
        response = Response(self.loop)
        logger.debug(f'call discover_characteristics({service["uuid"]})')
        self.discover_characteristics_async(
            response,
            service['start'],
            service['end'],
        )
        return await response.wait_receive()

    async def discover_descriptors(self, characteristic, *,
                                   start=0x0001, end=0xffff,
                                   filter_uuid=None) \
            -> List[dict]:
        response = Response(self.loop)
        logger.debug(f'call discover_descriptors({characteristic["uuid"]})')
        self.discover_descriptors_async(
            response,
            start,
            end,
            # characteristic['uuid'],
        )
        descriptors = await response.wait_receive()
        logger.debug(f' .. descriptors {descriptors}')

        if filter_uuid:
            descriptors = [d for d in descriptors if d['uuid'] == filter_uuid]

        return descriptors

    async def enable_notifications(self, handle, enable):
        response = Response(self.loop)
        logger.debug(f'call enable_notifications({handle}, {enable})')
        try:
            self.enable_notifications_async(
                handle,  # handle
                1 if enable else 0,  # notifications
                0,  # indications
                response,
            )
        except BTIOException as e:
            raise BleakGattlibIOError(str(e)) from None
        return await response.wait_receive()

    async def write_by_handle_(self, handle, data):
        response = Response(self.loop)
        # logger.debug(f'call write_by_handle({handle}, {data})')
        try:
            self.write_by_handle_async(handle, data, response)
        except BTIOException as e:
            raise BleakGattlibIOError(str(e)) from None
        return await response.wait_receive()

    async def read_by_handle_(self, handle):
        response = Response(self.loop)
        # logger.debug(f'call read_by_handle({handle})')
        try:
            self.read_by_handle_async(handle, response)
        except BTIOException as e:
            raise BleakGattlibIOError(str(e)) from None
        return (await response.wait_receive())[0]


class BleakClientGattlib(BaseBleakClient):

    def __init__(self, address_or_ble_device: Union[BLEDevice, str], **kwargs):
        super().__init__(address_or_ble_device, **kwargs)
        self._address_type = (
            kwargs["address_type"]
            if "address_type" in kwargs
            and kwargs["address_type"] in ("public", "random")
            else 'public'
        )
        self._device = kwargs.get('adapter', 'hci0')
        self._notification_callbacks = {}
        self._subscriptions: List[int] = []

        self._services = None

        self._requester: Optional[GATTRequester] = None

    async def connect(self, **kwargs) -> bool:
        logger.debug(
            f"Connecting to device @ {self.address}")

        if self.is_connected:
            raise BleakConnectionError("Client is already connected")

        timeout = kwargs.get("timeout", self._timeout)
        loop = asyncio.get_running_loop()

        try:
            self._requester = Requester(
                self,
                loop,
                self.address,
                False,
                self._device,
            )
            await self._requester.connect_(self._address_type, timeout)
        except (BTIOException, asyncio.TimeoutError) as e:
            if isinstance(e, BTIOException):
                raise BleakGattlibIOError(str(e)) from e
            raise BleakError(repr(e)) from e
        finally:
            logger.info(f'{self} disconnect!')
            if self._disconnected_callback:
                self._disconnected_callback(self)

        await asyncio.wait_for(self.get_services(asyncio.get_running_loop()),
                               10)

        return True

    async def disconnect(self) -> bool:
        if self._requester and self._requester.is_connected():
            try:
                await self._requester.disconnect_()
            except BTIOException as e:
                # logger.exception(f"Error in disconnect: {e}")
                raise BleakGattlibIOError(str(e)) from e
            finally:
                if self._disconnected_callback:
                    self._disconnected_callback(self)

        return True

    @property
    def is_connected(self) -> bool:
        if not self._requester:
            return False
        return self._requester.is_connected()

    async def start_notify(
            self,
            char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
            callback: Callable[[int, bytearray], None],
            **kwargs) -> None:
        if not self._requester:
            raise BleakConnectionError('Not connected')

        """Activate notifications/indications on a characteristic.

        Callbacks must accept two inputs. The first will be a integer handle of the characteristic generating the
        data and the second will be a ``bytearray`` containing the data sent from the connected server.

        .. code-block:: python

            def callback(sender: int, data: bytearray):
                print(f"{sender}: {data}")
            client.start_notify(char_uuid, callback)

        Args:
            char_specifier (BleakGATTCharacteristic, int, str or UUID): The characteristic to activate
                notifications/indications on a characteristic, specified by either integer handle,
                UUID or directly by the BleakGATTCharacteristic object representing it.
            callback (function): The function to be called on notification.

        """
        if inspect.iscoroutinefunction(callback):

            def bleak_callback(s, d):
                asyncio.ensure_future(callback(s, d))

        else:
            bleak_callback = callback

        manager: Requester = self._requester

        if not isinstance(char_specifier, BleakGATTCharacteristicGattlib):
            characteristic = self.services.get_characteristic(char_specifier)
        else:
            characteristic = char_specifier

        self._notification_callbacks[characteristic.handle] = bleak_callback
        self._subscriptions.append(characteristic.handle)

        # await manager.enable_notifications(characteristic.handle, True)
        desc = await manager.discover_descriptors(
            characteristic.obj,
            start=characteristic.handle + 1,
            end=0xffff,
            filter_uuid=DescriptorUUID.client_characteristic_configuration.as_uuid(),
        )
        await manager.enable_notifications(desc[0]['handle'], True)

    async def stop_notify(
        self,
        char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
    ) -> None:
        if not self._requester:
            raise BleakConnectionError('Not connected')

        manager: Requester = self._requester

        if not isinstance(char_specifier, BleakGATTCharacteristicGattlib):
            characteristic = self.services.get_characteristic(char_specifier)
        else:
            characteristic = char_specifier
        if not characteristic:
            raise BleakError("Characteristic {} not found!".format(char_specifier))

        desc = await manager.discover_descriptors(
            characteristic.obj,
            filter_uuid=DescriptorUUID.client_characteristic_configuration.as_uuid(),
        )
        await manager.enable_notifications(desc[0]['handle'], False)

        self._notification_callbacks.pop(characteristic.handle, None)
        try:
            self._subscriptions.remove(characteristic.handle)
        except ValueError:
            pass

    async def write_gatt_descriptor(self, handle: int, data: bytearray) -> None:
        pass

    async def write_gatt_char(
            self,
            char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
            data: bytearray,
            response: bool = False) -> None:
        """Perform a write operation of the specified GATT characteristic.

        Args:
            char_specifier (BleakGATTCharacteristic, int, str or UUID): The characteristic to write
                to, specified by either integer handle, UUID or directly by the
                BleakGATTCharacteristic object representing it.
            data (bytes or bytearray): The data to send.
            response (bool): If write-with-response operation should be done. Defaults to `False`.

        """
        manager: Requester = self._requester

        if not isinstance(char_specifier, BleakGATTCharacteristic):
            characteristic = self.services.get_characteristic(char_specifier)
        else:
            characteristic = char_specifier
        if not characteristic:
            raise BleakError("Characteristic {} was not found!".format(char_specifier))

        try:
            await manager.write_by_handle_(characteristic.handle, data)
            logger.debug(
                "Write Characteristic {0} : {1}".format(characteristic.uuid, data),
            )
        except BleakError as e:
            raise BleakError(
                "Could not write value {0} to characteristic {1}: {2}".format(
                    data, characteristic.uuid, e.args[0],
                ),
            )

    async def read_gatt_descriptor(self, handle: int, **kwargs) -> bytearray:
        pass

    async def read_gatt_char(
            self,
            char_specifier: Union[BleakGATTCharacteristic, int, str, uuid.UUID],
            **kwargs) -> bytearray:

        """Perform read operation on the specified GATT characteristic.

                Args:
                    char_specifier (BleakGATTCharacteristic, int, str or UUID): The characteristic to read from,
                        specified by either integer handle, UUID or directly by the
                        BleakGATTCharacteristic object representing it.
                    use_cached (bool): `False` forces macOS to read the value from the
                        device again and not use its own cached value. Defaults to `False`.

                Returns:
                    (bytearray) The read data.

                """
        manager = self._requester

        if not isinstance(char_specifier, BleakGATTCharacteristic):
            characteristic = self.services.get_characteristic(char_specifier)
        else:
            characteristic = char_specifier
        if not characteristic:
            raise BleakError(
                "Characteristic {} was not found!".format(char_specifier))

        try:
            output = await manager.read_by_handle_(characteristic.obj['value_handle'])
        except BTIOException as e:
            raise BleakGattlibIOError(str(e)) from e
        value = bytearray(output)
        logger.debug(
            "Read Characteristic {0} : {1}".format(characteristic.uuid, value))
        return value

    async def get_services(self, loop, **kwargs) -> BleakGATTServiceCollection:
        """Get all services registered for this GATT server.

        Returns:
           A :py:class:`bleak.backends.service.BleakGATTServiceCollection` with this device's services tree.

        """
        if self._services is not None:
            return self.services

        logger.debug("Retrieving services...")
        manager: Requester = self._requester

        async def _discover_characteristics(service):
            return service, await manager.discover_characteristics(service)

        async def _discover_descriptors(characteristic):
            return characteristic, await manager.discover_descriptors(
                characteristic,
            )

        services = await manager.discover_services()
        service_characteristics = []

        for service in services:
            logger.info(f'Get characteristic for {service["uuid"]}')
            try:
                service_characteristics.append(
                    await _discover_characteristics(service),
                )
            except BleakGattlibResponseError as e:
                logger.info(f'{service["uuid"]} returns '
                            f'{os.strerror(int(e.err_code))} ({e.err_code})')
            except BleakError as e:
                logger.exception(str(e))

        # service_characteristics = await asyncio.gather(*[
        #     _discover_characteristics(service)
        #     for service in services
        # ])



        # characteristic_descriptors = await asyncio.gather(*[
        #     _discover_descriptors(characteristic)
        #     for characteristic in (
        #         characteristic
        #         for _, characteristics in service_characteristics
        #         for characteristic in characteristics
        #     )
        # ])

        # all_descriptors = await manager.discover_descriptors()
        # characteristic['value_handle'] + 1,
        # ,

        for service, characteristics in service_characteristics:
            self.services.add_service(BleakGATTServiceGattlib(service))

            # characteristic_descriptors = await asyncio.gather(*[
            #     _discover_descriptors(characteristic)
            #     for characteristic in (
            #         characteristic
            #         for characteristic in characteristics
            #     )
            # ])

            # for characteristic, descriptors in characteristic_descriptors:
            for i, characteristic in enumerate(characteristics):
                # logger.debug(
                #     "Retrieving descriptors for characteristic {}".format(
                #         characteristic['uuid'],
                #     )
                # )
                # next_item = characteristics[i+1] if i < len(characteristics) else None
                #
                # descriptors = await manager.discover_descriptors(
                #     characteristic,
                # )

                self.services.add_characteristic(
                    BleakGATTCharacteristicGattlib(characteristic, service),
                )
                # print(service, characteristic)
                # for descriptor in descriptors:
                #     print(descriptor)
                #     self.services.add_descriptor(
                #         BleakGATTDescriptorGattlib(
                #             descriptor,
                #             characteristic['uuid'],
                #             characteristic['value_handle'],
                #         )
                #     )

        # for service in services:
        #     serviceUUID = service['uuid']
        #     logger.debug(
        #         "Retrieving characteristics for service {}".format(serviceUUID)
        #     )
        #     characteristics = await manager.discover_characteristics(service)
        #
        #     self.services.add_service(BleakGATTServiceGattlib(service))
        #
        #     for characteristic in characteristics:
        #     #     logger.debug(
        #     #         "Retrieving descriptors for characteristic {}".format(
        #     #             characteristic['uuid'],
        #     #         )
        #     #     )
        #     #     descriptors = await manager.discover_descriptors(characteristic)
        #         self.services.add_characteristic(
        #             BleakGATTCharacteristicGattlib(characteristic, service)
        #         )
        #     #     for descriptor in descriptors:
        #     #         self.services.add_descriptor(
        #     #             BleakGATTDescriptorGattlib(
        #     #                 descriptor,
        #     #                 characteristic['uuid'],
        #     #                 characteristic['handle'],
        #     #             )
        #     #         )
        logger.debug("Services resolved for %s", str(self))
        self._services_resolved = True
        self._services = services
        return self.services

    async def unpair(self) -> bool:
        pass

    async def pair(self, *args, **kwargs) -> bool:
        pass

    def push_notification(self, handle, data):
        logger.debug(f'notification from {handle}: {data}')
        callback = self._notification_callbacks.get(handle)
        if callback:
            # TODO: find character by value_handle and use its handle
            callback(int(handle) - 1, data)

import asyncio as aio
import json
import logging
import typing as ty
from uuid import getnode

import aio_mqtt
from bleak.backends.device import BLEDevice

from .compat import get_scanner
from .devices.base import ConnectionMode, Device, done_callback
from .exceptions import (ListOfConnectionErrors, handle_ble_exceptions,
                         restart_bluetooth)
from .manager import DeviceManager
from .tasks import handle_returned_tasks, run_tasks_and_cancel_on_first_return

try:
    from bleak import AdvertisementData
except ImportError:
    AdvertisementData = ty.Any

_LOGGER = logging.getLogger(__name__)

BRIDGE_STATE_TOPIC = 'state'


class Ble2Mqtt:
    TOPIC_ROOT = 'ble2mqtt'
    BRIDGE_TOPIC = 'bridge'

    def __init__(
            self,
            ssl,
            host: str,
            port: int = None,
            user: ty.Optional[str] = None,
            password: ty.Optional[str] = None,
            reconnection_interval: int = 10,
            loop: ty.Optional[aio.AbstractEventLoop] = None,
            *,
            hci_adapter: str,
            base_topic,
            mqtt_config_prefix,
            legacy_color_mode,
    ) -> None:
        self._hci_adapter = hci_adapter
        self._mqtt_host = host
        self._mqtt_port = port
        self._mqtt_user = user
        self._mqtt_password = password
        self._ssl = ssl
        self._base_topic = base_topic
        self._mqtt_config_prefix = mqtt_config_prefix
        self._legacy_color_mode = legacy_color_mode

        self._reconnection_interval = reconnection_interval
        self._loop = loop or aio.get_event_loop()

        self._mqtt_client = aio_mqtt.Client(
            client_id_prefix=f'{base_topic}_',
            loop=self._loop,
        )

        self._device_managers: ty.Dict[Device, DeviceManager] = {}

        self.availability_topic = '/'.join((
            self._base_topic,
            self.BRIDGE_TOPIC,
            BRIDGE_STATE_TOPIC,
        ))

        self.device_registry: ty.List[Device] = []

    async def start(self):
        result = await run_tasks_and_cancel_on_first_return(
            self._loop.create_task(self._connect_mqtt_forever()),
            self._loop.create_task(self._handle_messages()),
        )
        for t in result:
            await t

    async def close(self) -> None:
        for device, manager in self._device_managers.items():
            await manager.close()

        if self._mqtt_client.is_connected:
            try:
                await self._mqtt_client.disconnect()
            except aio.CancelledError:
                raise
            except aio_mqtt.ConnectionClosedError:
                pass
            except Exception as e:
                _LOGGER.warning(f'Error on MQTT disconnecting: {repr(e)}')

    def register(self, device_class: ty.Type[Device], *args, **kwargs):
        device = device_class(*args, **kwargs)
        if not device:
            return
        if not device.is_passive and not device.SUPPORT_ACTIVE:
            raise NotImplementedError(
                f'Device {device.dev_id} doesn\'t support active mode',
            )
        assert device.is_passive or device.ACTIVE_CONNECTION_MODE in (
            ConnectionMode.ACTIVE_POLL_WITH_DISCONNECT,
            ConnectionMode.ACTIVE_KEEP_CONNECTION,
            ConnectionMode.ON_DEMAND_CONNECTION,
        )
        self.device_registry.append(device)

    @property
    def subscribed_topics(self):
        return [
            '/'.join((self._base_topic, topic))
            for device in self.device_registry
            for topic in device.subscribed_topics
        ]

    async def _handle_messages(self) -> None:
        async for message in self._mqtt_client.delivered_messages(
            f'{self._base_topic}/#',
        ):
            _LOGGER.debug(message)
            while True:
                if message.topic_name not in self.subscribed_topics:
                    await aio.sleep(0)
                    continue

                prefix = f'{self._base_topic}/'
                if message.topic_name.startswith(prefix):
                    topic_wo_prefix = message.topic_name[len(prefix):]
                else:
                    topic_wo_prefix = prefix
                for _device in self.device_registry:
                    if topic_wo_prefix in _device.subscribed_topics:
                        device = _device
                        break
                else:
                    raise NotImplementedError('Unknown topic')
                await aio.sleep(0)
                if not device.client.is_connected:
                    _LOGGER.warning(
                        f'Received topic {topic_wo_prefix} '
                        f'with {message.payload} '
                        f' but {device.client} is offline',
                    )
                    await aio.sleep(5)
                    continue

                try:
                    value = json.loads(message.payload)
                except ValueError:
                    value = message.payload.decode()

                await device.add_incoming_message(topic_wo_prefix, value)
                break

            await aio.sleep(1)

    async def stop_device_manage_tasks(self):
        for manager in self._device_managers.values():
            try:
                await manager.close()
            except aio.CancelledError:
                raise
            except Exception:
                _LOGGER.exception(
                    f'Problem on closing dev manager {manager.device}')

    def device_detection_callback(self, device: BLEDevice,
                                  advertisement_data: AdvertisementData):
        for reg_device in self.device_registry:
            if reg_device.mac.lower() == device.address.lower():
                if hasattr(advertisement_data, 'rssi'):
                    rssi = advertisement_data.rssi
                else:
                    rssi = device.rssi
                if rssi:
                    # update rssi for all devices if available
                    reg_device.rssi = rssi

                if reg_device in self._device_managers:
                    self._device_managers[reg_device].set_scanned_device(device)

                if reg_device.is_passive:
                    if device.name:
                        reg_device._model = device.name
                    reg_device.handle_advert(device, advertisement_data)
                else:
                    _LOGGER.debug(
                        f'active device seen: {reg_device} '
                        f'{advertisement_data}',
                    )
                    reg_device.set_advertisement_seen()

    async def scan_devices_task(self):
        empty_scans = 0
        while True:
            # 10 empty scans in a row means that bluetooth restart is required
            if empty_scans >= 10:
                empty_scans = 0
                await restart_bluetooth(self._hci_adapter)

            try:
                async with handle_ble_exceptions(self._hci_adapter):
                    scanner = get_scanner(
                        self._hci_adapter,
                        self.device_detection_callback,
                    )
                    try:
                        await aio.wait_for(scanner.start(), 10)
                    except aio.TimeoutError:
                        _LOGGER.error('Scanner start failed with timeout')
                    await aio.sleep(3)
                    devices = scanner.discovered_devices
                    await scanner.stop()
                    if not devices:
                        empty_scans += 1
                    else:
                        empty_scans = 0
                    _LOGGER.debug(f'found {len(devices)} devices: {devices}')
            except KeyboardInterrupt:
                raise
            except aio.IncompleteReadError:
                raise
            except ListOfConnectionErrors as e:
                _LOGGER.exception(e)
                empty_scans += 1
            await aio.sleep(1)

    async def _run_device_tasks(self, mqtt_connection_fut: aio.Future) -> None:
        for dev in self.device_registry:
            self._device_managers[dev] = \
                DeviceManager(
                    dev,
                    hci_adapter=self._hci_adapter,
                    mqtt_client=self._mqtt_client,
                    base_topic=self._base_topic,
                    config_prefix=self._mqtt_config_prefix,
                    global_availability_topic=self.availability_topic,
                    legacy_color_mode=self._legacy_color_mode,
                )
        _LOGGER.debug("Wait for network interruptions...")

        device_tasks = [
            manager.run_task()
            for manager in self._device_managers.values()
        ]
        scan_task = self._loop.create_task(self.scan_devices_task())
        scan_task.add_done_callback(done_callback)
        device_tasks.append(scan_task)

        futs = [
            mqtt_connection_fut,
            *device_tasks,
        ]

        finished = await run_tasks_and_cancel_on_first_return(
            *futs,
            ignore_futures=[mqtt_connection_fut],
        )

        finished_managers = []
        for d, m in self._device_managers.items():
            if m.manage_task not in finished:
                await m.close()
            else:
                finished_managers.append(m)

        for m in finished_managers:
            await m.close()

        # when mqtt server disconnects, multiple tasks can raise
        # exceptions. We must fetch all of them
        finished = [t for t in futs if t.done() and not t.cancelled()]
        await handle_returned_tasks(*finished)

    async def _connect_mqtt_forever(self) -> None:
        dev_id = hex(getnode())
        while True:
            try:
                mqtt_connection = await aio.wait_for(self._mqtt_client.connect(
                    host=self._mqtt_host,
                    port=self._mqtt_port,
                    username=self._mqtt_user,
                    password=self._mqtt_password,
                    ssl=self._ssl,
                    client_id=f'ble2mqtt_{dev_id}',
                    will_message=aio_mqtt.PublishableMessage(
                        topic_name=self.availability_topic,
                        payload='offline',
                        qos=aio_mqtt.QOSLevel.QOS_1,
                        retain=True,
                    ),
                ), timeout=self._reconnection_interval)
                _LOGGER.info(f'Connected to {self._mqtt_host}')
                await self._mqtt_client.publish(
                    aio_mqtt.PublishableMessage(
                        topic_name=self.availability_topic,
                        payload='online',
                        qos=aio_mqtt.QOSLevel.QOS_1,
                        retain=True,
                    ),
                )
                await self._run_device_tasks(mqtt_connection.disconnect_reason)
            except aio.TimeoutError:
                logging.warning('Cannot connect to MQTT broker')
            except (aio.CancelledError, KeyboardInterrupt):
                if self._mqtt_client.is_connected():
                    await self._mqtt_client.publish(
                        aio_mqtt.PublishableMessage(
                            topic_name=self.availability_topic,
                            payload='offline',
                            qos=aio_mqtt.QOSLevel.QOS_0,
                            retain=True,
                        ),
                    )
                raise
            except Exception:
                _LOGGER.exception(
                    "Connection lost. Will retry in %d seconds.",
                    self._reconnection_interval,
                )
                try:
                    await self.stop_device_manage_tasks()
                except aio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.exception('Exception in _connect_forever()')
                try:
                    await self._mqtt_client.disconnect()
                except aio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.error('Disconnect from MQTT broker error')
                await aio.sleep(self._reconnection_interval)

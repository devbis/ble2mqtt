import asyncio as aio
import json
import logging
import typing as ty
from contextlib import asynccontextmanager
from uuid import getnode

import aio_mqtt
from bleak import BleakError, BleakScanner
from bleak.backends.device import BLEDevice

from .devices.base import (BINARY_SENSOR_DOMAIN, LIGHT_DOMAIN, SENSOR_DOMAIN,
                           SWITCH_DOMAIN, Device)

logger = logging.getLogger(__name__)

CONFIG_MQTT_NAMESPACE = 'homeassistant'
SENSOR_STATE_TOPIC = 'state'
BLUETOOTH_ERROR_RECONNECTION_TIMEOUT = 60
FAILURE_LIMIT = 5


ListOfConnectionErrors = (
    BleakError,
    aio.TimeoutError,

    # dbus-next exceptions:
    # AttributeError: 'NoneType' object has no attribute 'call'
    AttributeError,
    # https://github.com/hbldh/bleak/issues/409
    EOFError,
)


async def run_tasks_and_cancel_on_first_return(*tasks: ty.Set[aio.Future],
                                               return_when=aio.FIRST_COMPLETED,
                                               ) -> ty.Set[aio.Future]:
    try:
        done, pending = await aio.wait(tasks, return_when=return_when)
    except aio.CancelledError:
        for t in tasks:
            if isinstance(t, aio.Task) and not t.done():
                t.cancel()
                try:
                    await t
                except aio.CancelledError:
                    pass
        raise

    for t in pending:
        if isinstance(t, aio.Task):
            t.cancel()
    for t in pending:
        if isinstance(t, aio.Task):
            try:
                await t
            except aio.CancelledError:
                pass
    return done


def hardware_exception_occurred(exception):
    ex_str = str(exception)
    return (
        'org.freedesktop.DBus.Error.ServiceUnknown' in ex_str or
        'org.freedesktop.DBus.Error.NoReply' in ex_str or
        'org.freedesktop.DBus.Error.AccessDenied' in ex_str or
        'org.bluez.Error.NotReady' in ex_str or
        'org.bluez.Error.InProgress' in ex_str
    )


ListOfMQTTConnectionErrors = (
        aio_mqtt.ConnectionLostError,
        aio_mqtt.ConnectionClosedError,
        aio_mqtt.ServerDiedError,
        BrokenPipeError,
)


class Ble2Mqtt:
    TOPIC_ROOT = 'ble2mqtt'
    BRIDGE_TOPIC = 'bridge'

    def __init__(
            self,
            host: str,
            port: int = None,
            user: ty.Optional[str] = None,
            password: ty.Optional[str] = None,
            reconnection_interval: int = 10,
            loop: ty.Optional[aio.AbstractEventLoop] = None,
    ) -> None:
        self._mqtt_host = host
        self._mqtt_port = port
        self._mqtt_user = user
        self._mqtt_password = password

        self._reconnection_interval = reconnection_interval
        self._loop = loop or aio.get_event_loop()
        self._mqtt_client = aio_mqtt.Client(
            client_id_prefix='ble2mqtt_',
            loop=self._loop,
        )

        self._device_manage_tasks = {}

        self.availability_topic = '/'.join((
            self.TOPIC_ROOT,
            self.BRIDGE_TOPIC,
            SENSOR_STATE_TOPIC,
        ))

        self.device_registry: ty.List[Device] = []
        self.bluetooth_restarting = aio.Lock()

    async def start(self):
        result = await run_tasks_and_cancel_on_first_return(
            self._loop.create_task(self._connect_forever()),
            self._loop.create_task(self._handle_messages()),
        )
        for t in result:
            t.result()

    @staticmethod
    async def stop_task(task):
        logger.debug(f'stop_task task={task}')
        task.cancel()
        try:
            await task
        except aio.CancelledError:
            pass

    async def close(self) -> None:
        tasks = []
        devices = []
        for device, task in self._device_manage_tasks.items():
            tasks.append(aio.create_task(self.stop_task(task)))
            devices.append(device)
        await aio.gather(*tasks, return_exceptions=True)
        await aio.gather(
            *[aio.create_task(device.close()) for device in devices],
            return_exceptions=True,
        )

        if self._mqtt_client.is_connected:
            try:
                await self._mqtt_client.disconnect()
            except (aio_mqtt.Error, BrokenPipeError):
                pass

    def _get_topic(self, dev_id, subtopic, *args):
        return '/'.join((self.TOPIC_ROOT, dev_id, subtopic, *args))

    def register(self, device: Device):
        if not device:
            return
        if not device.passive and not device.SUPPORT_ACTIVE:
            raise NotImplementedError(
                f'Device {device.dev_id} doesn\'t support active mode',
            )
        self.device_registry.append(device)

    @property
    def subscribed_topics(self):
        return [
            '/'.join((self.TOPIC_ROOT, topic))
            for device in self.device_registry
            for topic in device.subscribed_topics
        ]

    async def publish_topic_callback(self, topic, value):
        logger.debug(f'call publish callback topic={topic} value={value}')
        await self._mqtt_client.publish(
            aio_mqtt.PublishableMessage(
                topic_name='/'.join((self.TOPIC_ROOT, topic)),
                payload=value,
                qos=aio_mqtt.QOSLevel.QOS_1,
            ),
        )

    async def _handle_messages(self) -> None:
        async for message in self._mqtt_client.delivered_messages(
            f'{self.TOPIC_ROOT}/#',
        ):
            logger.debug(message)
            while True:
                if message.topic_name not in self.subscribed_topics:
                    continue

                prefix = f'{self.TOPIC_ROOT}/'
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
                if not device.client.is_connected:
                    logger.warning(
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

    async def send_device_config(self, device: Device):
        device_info = {
            'identifiers': [
                device.unique_id,
            ],
            'name': device.unique_id,
            'model': device.model,
            'manufacturer': device.manufacturer,
        }
        if device.version:
            device_info['sw_version'] = device.version

        def get_generic_vals(entity: dict):
            name = entity.pop('name')
            result = {
                'name': f'{name}_{device.dev_id}',
                'unique_id': f'{name}_{device.dev_id}',
                'device': device_info,
            }
            icon = entity.pop('icon', None)
            if icon:
                result['icon'] = f'mdi:{icon}'
            entity.pop('topic', None)
            entity.pop('json', None)
            entity.pop('main_value', None)
            result.update(entity)
            return result

        messages_to_send = []
        sensor_entities = device.entities.get(SENSOR_DOMAIN, [])
        sensor_entities.append(
            {
                'name': 'linkquality',
                'unit_of_measurement': 'lqi',
                'icon': 'signal',
            },
        )
        entities = {
            **device.entities,
            SENSOR_DOMAIN: sensor_entities,
        }
        for cls, entities in entities.items():
            if cls in (BINARY_SENSOR_DOMAIN, SENSOR_DOMAIN):
                for entity in entities:
                    entity_name = entity['name']
                    state_topic = self._get_topic(
                        device.unique_id,
                        entity.get('topic', SENSOR_STATE_TOPIC),
                    )
                    config_topic = '/'.join((
                        CONFIG_MQTT_NAMESPACE,
                        cls,
                        device.dev_id,
                        entity_name,
                        'config',
                    ))
                    if entity.get('json') and entity.get('main_value'):
                        state_topic_part = {
                            'json_attributes_topic': state_topic,
                            'state_topic': state_topic,
                            'value_template':
                                f'{{{{ value_json.{entity["main_value"]} }}}}',
                        }
                    else:
                        state_topic_part = {
                            'state_topic': state_topic,
                            'value_template':
                                f'{{{{ value_json.{entity_name} }}}}',
                        }

                    payload = json.dumps({
                        **get_generic_vals(entity),
                        **state_topic_part,
                    })
                    logger.debug(
                        f'Publish config topic={config_topic}: {payload}',
                    )
                    messages_to_send.append(
                        aio_mqtt.PublishableMessage(
                            topic_name=config_topic,
                            payload=payload,
                            qos=aio_mqtt.QOSLevel.QOS_1,
                            retain=True,
                        ),
                    )
            if cls == SWITCH_DOMAIN:
                for entity in entities:
                    entity_name = entity['name']
                    state_topic = self._get_topic(device.unique_id, entity_name)
                    command_topic = '/'.join((state_topic, device.SET_POSTFIX))
                    config_topic = '/'.join((
                        CONFIG_MQTT_NAMESPACE,
                        cls,
                        device.dev_id,
                        entity_name,
                        'config',
                    ))
                    payload = json.dumps({
                        **get_generic_vals(entity),
                        'state_topic': state_topic,
                        'command_topic': command_topic,
                    })
                    logger.debug(
                        f'Publish config topic={config_topic}: {payload}',
                    )
                    messages_to_send.append(
                        aio_mqtt.PublishableMessage(
                            topic_name=config_topic,
                            payload=payload,
                            qos=aio_mqtt.QOSLevel.QOS_1,
                            retain=True,
                        ),
                    )
                    # TODO: send real state on receiving status from a device
                    logger.debug(f'Publish initial state topic={state_topic}')
                    await self._mqtt_client.publish(
                        aio_mqtt.PublishableMessage(
                            topic_name=state_topic,
                            payload='OFF',
                            qos=aio_mqtt.QOSLevel.QOS_1,
                        ),
                    )
            if cls == LIGHT_DOMAIN:
                for entity in entities:
                    entity_name = entity['name']
                    state_topic = self._get_topic(device.unique_id, entity_name)
                    set_topic = self._get_topic(
                        device.unique_id,
                        entity_name,
                        device.SET_POSTFIX,
                    )
                    config_topic = '/'.join((
                        CONFIG_MQTT_NAMESPACE,
                        cls,
                        device.dev_id,
                        entity_name,
                        'config',
                    ))
                    payload = json.dumps({
                        **get_generic_vals(entity),
                        'schema': 'json',
                        'rgb': entity.get('rgb', True),
                        'brightness': entity.get('brightness', True),
                        'state_topic': state_topic,
                        'command_topic': set_topic,
                    })
                    logger.debug(
                        f'Publish config topic={config_topic}: {payload}',
                    )
                    messages_to_send.append(
                        aio_mqtt.PublishableMessage(
                            topic_name=config_topic,
                            payload=payload,
                            qos=aio_mqtt.QOSLevel.QOS_1,
                            retain=True,
                        ),
                    )
        await aio.gather(*[
            self._mqtt_client.publish(message)
            for message in messages_to_send
        ])
        device.config_sent = True

    async def restart_bluetooth(self):
        # EXPERIMENTAL: restart bluetooth on errors
        if self.bluetooth_restarting.locked():
            await aio.sleep(9)
            return
        async with self.bluetooth_restarting:
            logger.warning('Restarting bluetoothd...')
            proc = await aio.create_subprocess_exec(
                'hciconfig', 'hci0', 'down',
            )
            await proc.wait()
            proc = await aio.create_subprocess_exec(
                '/etc/init.d/bluetoothd', 'restart',
            )
            await proc.wait()
            await aio.sleep(3)
            proc = await aio.create_subprocess_exec(
                'hciconfig', 'hci0', 'up',
            )
            await proc.wait()
            await aio.sleep(5)
            logger.warning('Restarting bluetoothd finished')

    @asynccontextmanager
    async def handle_ble_exceptions(self):
        try:
            yield
        except ListOfConnectionErrors as e:
            if hardware_exception_occurred(e):
                await self.restart_bluetooth()
                await aio.sleep(3)
            raise

    async def manage_device(self, device: Device):
        logger.debug(f'Start managing device={device}')
        failure_count = 0
        missing_device_count = 0
        while True:
            tasks = []
            async with self.bluetooth_restarting:
                logger.debug(f'[{device}] Check for lock')
            try:
                async with self.handle_ble_exceptions():
                    await device.connect()
                    initial_coros = []
                    if not device.passive:
                        if not device.DEVICE_DROPS_CONNECTION:
                            initial_coros.append(device.disconnected_event.wait)
                        await device.get_device_data()
                        failure_count = 0
                        missing_device_count = 0

                    if device.subscribed_topics:
                        await self._mqtt_client.subscribe(*[
                            (
                                '/'.join((self.TOPIC_ROOT, topic)),
                                aio_mqtt.QOSLevel.QOS_1,
                            )
                            for topic in device.subscribed_topics
                        ])
                    logger.debug(f'[{device}] mqtt subscribed')
                    tasks = [
                        self._loop.create_task(
                            device.handle(
                                self.publish_topic_callback,
                                send_config=self.send_device_config,
                            ),
                        ),
                        *[aio.create_task(coro()) for coro in initial_coros],
                    ]
                    will_handle_messages = bool(device.subscribed_topics)
                    if will_handle_messages:
                        tasks.append(self._loop.create_task(
                            device.handle_messages(self.publish_topic_callback),
                        ))

                    logger.debug(f'[{device}] tasks are created')

                    finished = await run_tasks_and_cancel_on_first_return(
                        *tasks,
                    )
                    if device.disconnected_event.is_set():
                        logger.debug(f'{device} has disconnected')
                    for t in finished:
                        logger.debug(
                            f'Fetching result device={device}, task={t}',
                        )
                        t.result()
            except aio.CancelledError:
                raise
            except KeyboardInterrupt:
                raise
            except (ConnectionError, TimeoutError, aio.TimeoutError):
                missing_device_count += 1
                logger.exception(
                    f'[{device}] connection problem, '
                    f'attempts={missing_device_count}',
                )
            except ListOfConnectionErrors as e:
                if 'Device with address' in str(e) and \
                        'was not found' in str(e):
                    missing_device_count += 1
                    logger.warning(
                        f'Error while connecting to {device}, {e} {repr(e)}, '
                        f'attempts={missing_device_count}',
                    )
                else:
                    if isinstance(e, aio.TimeoutError):
                        failure_count += 1
                    logger.warning(
                        f'Error while connecting to {device}, {e} {repr(e)}, '
                        f'failure_count={failure_count}',
                    )

                # sometimes LYWSD03MMC devices remain connected
                # and doesn't advert their presence.
                # If cannot find device for several attempts, restart
                # the bluetooth chip
                if missing_device_count >= device.CONNECTION_FAILURES_LIMIT:
                    logger.error(
                        f'Device {device} was not found for '
                        f'{missing_device_count} times. Restarting bluetooth.',
                    )
                    missing_device_count = 0
                    await self.restart_bluetooth()
            finally:
                try:
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                            try:
                                await t
                            except aio.CancelledError:
                                pass

                    await device.close()
                except aio.CancelledError:
                    raise
                except Exception:
                    pass

            if failure_count >= FAILURE_LIMIT:
                await self.restart_bluetooth()
                failure_count = 0
            try:
                await device.close()
                if not device.disconnected_event.is_set():
                    await aio.wait_for(
                        device.disconnected_event.wait(),
                        timeout=10,
                    )
            except aio.TimeoutError:
                logger.exception(f'{device} not disconnected in 10 secs')
            logger.debug(
                f'Sleep for {device.RECONNECTION_SLEEP_INTERVAL} secs to '
                f'reconnect to device={device}',
            )
            await aio.sleep(device.RECONNECTION_SLEEP_INTERVAL)

    async def create_device_manage_tasks(self):
        tasks = []
        for dev in self.device_registry:
            assert not self._device_manage_tasks.get(dev) or \
                self._device_manage_tasks[dev].done()
            task = self._loop.create_task(self.manage_device(dev))
            self._device_manage_tasks[dev] = task
            tasks.append(task)
        return tasks

    async def stop_device_manage_tasks(self):
        for dev in list(self._device_manage_tasks.keys()):
            logger.info(f'Stopping manage task for device {dev}')
            task = self._device_manage_tasks.pop(dev)
            await self.stop_task(task)
            try:
                await dev.close()
            except aio.CancelledError:
                raise
            except Exception:
                logger.exception(f'Error on closing dev {dev}')

    def device_detection_callback(self, device: BLEDevice, advertisement_data):
        for reg_device in self.device_registry:
            if reg_device.mac.lower() == device.address.lower():
                if device.rssi:
                    # update rssi for all devices if available
                    reg_device.rssi = device.rssi
                if reg_device.passive:
                    if device.name:
                        reg_device._model = device.name
                    reg_device.handle_advert(device, advertisement_data)

    async def scan_devices_task(self):
        while True:
            try:
                async with self.handle_ble_exceptions():
                    async with BleakScanner(scanning_mode='passive') as scanner:
                        scanner.register_detection_callback(
                            self.device_detection_callback,
                        )
                        await aio.sleep(3.0)
                        devices = await scanner.get_discovered_devices()
                    logger.debug(f'found {len(devices)} devices')
            except KeyboardInterrupt:
                raise
            except aio.IncompleteReadError:
                raise
            except ListOfConnectionErrors as e:
                logger.exception(e)
            await aio.sleep(1)

    async def _run_device_tasks(self, mqtt_connection_fut: aio.Future) -> None:
        tasks = await self.create_device_manage_tasks()
        logger.debug("Wait for network interruptions...")

        finished = await run_tasks_and_cancel_on_first_return(
            mqtt_connection_fut,
            self._loop.create_task(self.scan_devices_task()),
            *tasks,
        )
        for t in finished:
            t.result()

    async def _connect_forever(self) -> None:
        dev_id = hex(getnode())
        while True:
            try:
                mqtt_connection = await self._mqtt_client.connect(
                    host=self._mqtt_host,
                    port=self._mqtt_port,
                    username=self._mqtt_user,
                    password=self._mqtt_password,
                    client_id=f'ble2mqtt_{dev_id}',
                    will_message=aio_mqtt.PublishableMessage(
                        topic_name=self.availability_topic,
                        payload='offline',
                        qos=aio_mqtt.QOSLevel.QOS_1,
                        retain=True,
                    ),
                )
                logger.info(f'Connected to {self._mqtt_host}')
                await self._mqtt_client.publish(
                    aio_mqtt.PublishableMessage(
                        topic_name=self.availability_topic,
                        payload='online',
                        qos=aio_mqtt.QOSLevel.QOS_1,
                        retain=True,
                    ),
                )
                await self._run_device_tasks(mqtt_connection.disconnect_reason)
            except aio.CancelledError:
                raise
            except KeyboardInterrupt:
                raise
            except Exception as e:
                logger.exception(
                    "Connection lost. Will retry in %d seconds. %s",
                    self._reconnection_interval,
                    e,
                )
                try:
                    await self._mqtt_client.disconnect()
                except aio_mqtt.Error:
                    pass
                try:
                    await self.stop_device_manage_tasks()
                except Exception as e1:
                    logger.exception(e1)
                await aio.sleep(self._reconnection_interval)

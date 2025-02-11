import asyncio as aio
import json
import logging
import typing as ty

import aio_mqtt
from bleak.backends.device import BLEDevice

from ble2mqtt.__version__ import VERSION

from .devices.base import (BINARY_SENSOR_DOMAIN, BUTTON_DOMAIN, CLIMATE_DOMAIN,
                           COVER_DOMAIN, DEVICE_TRACKER_DOMAIN, LIGHT_DOMAIN,
                           SELECT_DOMAIN, SENSOR_DOMAIN, SWITCH_DOMAIN,
                           ConnectionMode, ConnectionTimeoutError, Device)
from .exceptions import (BLUETOOTH_RESTARTING, ListOfConnectionErrors,
                         ListOfMQTTConnectionErrors, handle_ble_exceptions,
                         restart_bluetooth)
from .tasks import handle_returned_tasks, run_tasks_and_cancel_on_first_return

_LOGGER = logging.getLogger(__name__)

CONFIG_MQTT_NAMESPACE = 'homeassistant'
FAILURE_LIMIT = 5


class DeviceManager:
    def __init__(self, device, *, hci_adapter, mqtt_client, base_topic,
                 config_prefix, global_availability_topic, legacy_color_mode):
        self.device: Device = device
        self._hci_adapter = hci_adapter
        self._mqtt_client = mqtt_client
        self._base_topic = base_topic
        self._config_prefix = config_prefix
        self._global_availability_topic = global_availability_topic
        self._legacy_color_mode = legacy_color_mode
        self.manage_task = None
        self.last_connection_successful = True

        self._scanned_device: ty.Union[BLEDevice, None] = None
        self._scanned_device_set = aio.Event()

    def set_scanned_device(self, ble_device: BLEDevice):
        self._scanned_device = ble_device
        self._scanned_device_set.set()

    async def close(self):
        if self.manage_task and not self.manage_task.done():
            self.manage_task.cancel()
            try:
                await self.manage_task
            except aio.CancelledError:
                pass
        self.manage_task = None
        try:
            await self.device.close()
        except aio.CancelledError:
            raise
        except Exception:
            _LOGGER.exception(f'Problem on closing device {self.device}')

    def run_task(self) -> aio.Task:
        assert not self.manage_task, \
            f'{self.device} Previous task was not finished! {self.manage_task}'
        self.manage_task = aio.create_task(self.manage_device())
        return self.manage_task

    async def publish_topic_callback(self, topic, value, nowait=False):
        _LOGGER.debug(f'call publish callback topic={topic} value={value}')
        if not self._mqtt_client.is_connected():
            _LOGGER.warning(f'{self.device} mqtt is disconnected')
            return
        try:
            await self._mqtt_client.publish(
                aio_mqtt.PublishableMessage(
                    topic_name='/'.join((self._base_topic, topic)),
                    payload=value,
                    qos=aio_mqtt.QOSLevel.QOS_1,
                ),
                nowait=nowait,
            )
        except ListOfMQTTConnectionErrors:
            _LOGGER.exception('Error while publishing to MQTT')

    def _get_topic(self, dev_id, subtopic, *args):
        return '/'.join(
            filter(None, (self._base_topic, dev_id, subtopic, *args)),
        )

    @property
    def _config_device_topic(self):
        """Add a prefix to avoid interfering with other ble software"""

        return f'{self._config_prefix}{self.device.dev_id}'

    async def send_device_config(self):
        device = self.device
        device_info = {
            'identifiers': [
                device.unique_id,
            ],
            'name': device.unique_name,
            'model': device.model,
        }
        if device.manufacturer:
            device_info['manufacturer'] = device.manufacturer
        if device.version:
            device_info['sw_version'] = device.version
        if device.suggested_area:
            device_info['suggested_area'] = device.suggested_area

        def get_generic_vals(entity: dict):
            name = entity.pop('name')
            result = {
                'name': f'{name}_{device.friendly_id}',
                'unique_id': f'{name}_{device.dev_id}',
                'device': device_info,
                'availability_mode': 'all',
                'availability': [
                    {'topic': self._global_availability_topic},
                    {'topic': '/'.join(
                        (self._base_topic, self.device.availability_topic),
                    )},
                ],
                'origin': {'name': 'ble2mqtt', 'sw_version': VERSION},
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
        for cls, entities in device.entities_with_lqi.items():
            if cls in (
                BINARY_SENSOR_DOMAIN,
                SENSOR_DOMAIN,
                DEVICE_TRACKER_DOMAIN,
            ):
                for entity in entities:
                    entity_name = entity['name']
                    state_topic = self._get_topic(
                        device.unique_id,
                        entity.get('topic', device.STATE_TOPIC),
                    )
                    config_topic = '/'.join((
                        CONFIG_MQTT_NAMESPACE,
                        cls,
                        self._config_device_topic,
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

                    if cls == DEVICE_TRACKER_DOMAIN:
                        state_topic_part['source_type'] = 'bluetooth_le'

                    payload = json.dumps({
                        **get_generic_vals(entity),
                        **state_topic_part,
                    })
                    _LOGGER.debug(
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
            if cls in {BUTTON_DOMAIN, SWITCH_DOMAIN}:
                has_state = cls == SWITCH_DOMAIN
                for entity in entities:
                    entity_name = entity['name']
                    state_topic = self._get_topic(
                        device.unique_id,
                        entity.get('topic', device.STATE_TOPIC),
                    )
                    command_topic = '/'.join((state_topic, device.SET_POSTFIX))
                    config_topic = '/'.join((
                        CONFIG_MQTT_NAMESPACE,
                        cls,
                        self._config_device_topic,
                        entity_name,
                        'config',
                    ))
                    payload = json.dumps({
                        **get_generic_vals(entity),
                        **({'state_topic': state_topic} if has_state else {}),
                        'command_topic': command_topic,
                    })
                    _LOGGER.debug(
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
                    if has_state:
                        # TODO: send real state on receiving status
                        # from a device
                        _LOGGER.debug(
                            f'Publish initial state topic={state_topic}')
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
                    state_topic = self._get_topic(
                        device.unique_id,
                        entity.get('topic', device.STATE_TOPIC),
                    )
                    set_topic = '/'.join((state_topic, device.SET_POSTFIX))
                    config_topic = '/'.join((
                        CONFIG_MQTT_NAMESPACE,
                        cls,
                        self._config_device_topic,
                        entity_name,
                        'config',
                    ))
                    color_mode = entity['color_mode']
                    payload = json.dumps({
                        **get_generic_vals(entity),
                        'schema': 'json',
                        **(
                            {'color_mode': bool(color_mode)}
                            if self._legacy_color_mode
                            else {}
                        ),
                        'supported_color_modes': entity.get(
                            'supported_color_modes', [color_mode],
                        ),
                        'brightness': entity.get('brightness', True),
                        'state_topic': state_topic,
                        'command_topic': set_topic,
                    })
                    _LOGGER.debug(
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
            if cls == COVER_DOMAIN:
                for entity in entities:
                    entity_name = entity['name']
                    state_topic = self._get_topic(
                        device.unique_id,
                        entity.get('topic', device.STATE_TOPIC),
                    )
                    set_topic = '/'.join((state_topic, device.SET_POSTFIX))
                    set_position_topic = '/'.join(
                        (state_topic, device.SET_POSITION_POSTFIX),
                    )
                    config_topic = '/'.join((
                        CONFIG_MQTT_NAMESPACE,
                        cls,
                        self._config_device_topic,
                        entity_name,
                        'config',
                    ))
                    config_params = {
                        **get_generic_vals(entity),
                        'state_topic': state_topic,
                        'position_topic': state_topic,
                        'json_attributes_topic': state_topic,
                        'value_template': '{{ value_json.state }}',
                        'position_template': '{{ value_json.position }}',
                        'command_topic': set_topic,
                        'set_position_topic': set_position_topic,
                    }
                    payload = json.dumps(config_params)
                    _LOGGER.debug(
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
            if cls == SELECT_DOMAIN:
                for entity in entities:
                    entity_name = entity['name']
                    state_topic = self._get_topic(
                        device.unique_id,
                        entity.get('topic', device.STATE_TOPIC),
                    )
                    set_topic = '/'.join((state_topic, device.SET_POSTFIX))
                    config_topic = '/'.join((
                        CONFIG_MQTT_NAMESPACE,
                        cls,
                        self._config_device_topic,
                        entity_name,
                        'config',
                    ))
                    config_params = {
                        **get_generic_vals(entity),
                        'state_topic': state_topic,
                        'command_topic': set_topic,
                    }
                    payload = json.dumps(config_params)
                    _LOGGER.debug(
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
            if cls == CLIMATE_DOMAIN:
                for entity in entities:
                    entity_name = entity['name']
                    state_topic = self._get_topic(
                        device.unique_id,
                        entity.get('topic', device.STATE_TOPIC),
                    )
                    mode_command_topic = '/'.join(
                        (state_topic, device.SET_MODE_POSTFIX),
                    )
                    temperature_command_topic = '/'.join(
                        (state_topic, device.SET_TARGET_TEMPERATURE_POSTFIX),
                    )
                    config_topic = '/'.join((
                        CONFIG_MQTT_NAMESPACE,
                        cls,
                        self._config_device_topic,
                        entity_name,
                        'config',
                    ))
                    config_params = {
                        **get_generic_vals(entity),
                        'current_temperature_topic': state_topic,
                        'current_temperature_template':
                            '{{ value_json.temperature }}',
                        'mode_state_topic': state_topic,
                        'mode_state_template': '{{ value_json.mode }}',
                        'mode_command_topic': mode_command_topic,
                        'temperature_state_topic': state_topic,
                        'temperature_state_template':
                            '{{ value_json.target_temperature }}',
                        'temperature_command_topic': temperature_command_topic,
                        'json_attributes_topic': state_topic,
                        'temp_step': 0.5,
                    }
                    payload = json.dumps(config_params)
                    _LOGGER.debug(
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

    async def send_availability(self, value: bool):
        return await self.device.send_availability(
            self.publish_topic_callback,
            value,
        )

    async def _sleep_until_next_connection(self):
        device = self.device
        _LOGGER.debug(
            f'Sleep for {device.RECONNECTION_SLEEP_INTERVAL} secs to '
            f'reconnect to device={device}',
        )
        if (
            device._connection_mode == ConnectionMode.ACTIVE_KEEP_CONNECTION or
            # if last connection failed, connect as soon as device appears,
            # don't wait for RECONNECTION_SLEEP_INTERVAL seconds
            not self.last_connection_successful
        ):
            try:
                await aio.wait_for(
                    device._advertisement_seen.wait(),
                    timeout=device.RECONNECTION_SLEEP_INTERVAL,
                )
            except aio.TimeoutError:
                pass
        else:
            await aio.sleep(self.device.RECONNECTION_SLEEP_INTERVAL)

    async def publish_topic_with_availability(self, topic, value):
        # call sequentially to allow HA receive a new value
        await self.publish_topic_callback(topic, value)
        await self.send_availability(True)

    async def manage_device(self):
        device = self.device
        _LOGGER.debug(f'Start managing device={device}')
        failure_count = 0
        missing_device_count = 0
        while True:
            async with BLUETOOTH_RESTARTING:
                _LOGGER.debug(f'[{device}] Check for lock')
            try:
                self.last_connection_successful = False
                if not device.is_passive:
                    try:
                        await aio.wait_for(
                            self._scanned_device_set.wait(),
                            timeout=10,
                        )
                    except aio.TimeoutError as e:
                        raise ConnectionTimeoutError(
                            f'[{device}] is not visible for 10 sec',
                        ) from e
                async with handle_ble_exceptions(self._hci_adapter):
                    await device.connect(
                        self._hci_adapter,
                        self._scanned_device,
                    )
                    self.last_connection_successful = True
                    initial_coros = []
                    if not device.is_passive:
                        if not device.DEVICE_DROPS_CONNECTION:
                            initial_coros.append(device.disconnected_event.wait)
                        await device.get_device_data()
                        failure_count = 0
                        missing_device_count = 0

                    if device.subscribed_topics:
                        await self._mqtt_client.subscribe(*[
                            (
                                '/'.join((self._base_topic, topic)),
                                aio_mqtt.QOSLevel.QOS_1,
                            )
                            for topic in device.subscribed_topics
                        ])
                    _LOGGER.debug(f'[{device}] mqtt subscribed')
                    coros = [
                        *[coro() for coro in initial_coros],
                        device.handle(
                            self.publish_topic_with_availability,
                            send_config=self.send_device_config,
                        ),
                    ]
                    will_handle_messages = bool(device.subscribed_topics)
                    if will_handle_messages:
                        coros.append(
                            device.handle_messages(
                                self.publish_topic_with_availability,
                            ),
                        )

                    tasks = [aio.create_task(t) for t in coros]
                    _LOGGER.debug(f'[{device}] tasks are created')

                    await run_tasks_and_cancel_on_first_return(*tasks)
                    if device.disconnected_event.is_set():
                        _LOGGER.debug(f'{device} has disconnected')
                    finished = [t for t in tasks if not t.cancelled()]
                    await handle_returned_tasks(*finished)
            except aio.CancelledError:
                # the only way to send availability=False on program shutdown
                if self.device.ACTIVE_CONNECTION_MODE in (
                    ConnectionMode.ACTIVE_POLL_WITH_DISCONNECT,
                    ConnectionMode.ON_DEMAND_CONNECTION,
                ):
                    try:
                        await aio.wait_for(
                            self.send_availability(False),
                            timeout=1,
                        )
                    except aio.TimeoutError:
                        pass
                raise
            except KeyboardInterrupt:
                raise
            except ConnectionTimeoutError:
                missing_device_count += 1
                _LOGGER.error(
                    f'[{device}] connection problem, '
                    f'attempts={missing_device_count}',
                )
            except (ConnectionError, TimeoutError, aio.TimeoutError):
                missing_device_count += 1
                _LOGGER.exception(
                    f'[{device}] connection problem, '
                    f'attempts={missing_device_count}',
                )
            except ListOfConnectionErrors as e:
                if 'Device with address' in str(e) and \
                        'was not found' in str(e):
                    missing_device_count += 1
                    _LOGGER.warning(
                        f'Error while connecting to {device}, {e} {repr(e)}, '
                        f'attempts={missing_device_count}',
                    )
                else:
                    # if isinstance(e, aio.TimeoutError) or \
                    #         'org.bluez.Error.Failed: Connection aborted' in \
                    #         str(e):
                    failure_count += 1
                    _LOGGER.warning(
                        f'Error while connecting to {device}, {e} {repr(e)}, '
                        f'failure_count={failure_count}',
                    )

                # sometimes LYWSD03MMC devices remain connected
                # and doesn't advert their presence.
                # If cannot find device for several attempts, restart
                # the bluetooth chip
                if missing_device_count >= device.CONNECTION_FAILURES_LIMIT:
                    _LOGGER.error(
                        f'Device {device} was not found for '
                        f'{missing_device_count} times. Restarting bluetooth.',
                    )
                    missing_device_count = 0
                    await restart_bluetooth(self._hci_adapter)
            finally:
                if self.device.ACTIVE_CONNECTION_MODE not in (
                    ConnectionMode.ACTIVE_POLL_WITH_DISCONNECT,
                    ConnectionMode.ON_DEMAND_CONNECTION,
                ):
                    try:
                        await aio.wait_for(
                            self.send_availability(False),
                            timeout=1,
                        )
                    except aio.TimeoutError:
                        pass
                try:
                    await aio.wait_for(device.close(), timeout=5)
                except aio.CancelledError:
                    raise
                except Exception:
                    _LOGGER.exception(f'{device} problem on device.close()')
                try:
                    canceled = []
                    for t in tasks:
                        if not t.done():
                            t.cancel()
                            canceled.append(t)
                    for t in canceled:
                        try:
                            t.result()
                        except aio.CancelledError:
                            pass
                except aio.CancelledError:
                    raise
                except Exception:
                    pass

            if failure_count >= FAILURE_LIMIT:
                await restart_bluetooth(self._hci_adapter)
                failure_count = 0
            try:
                if not device.disconnected_event.is_set():
                    await aio.wait_for(
                        device.disconnected_event.wait(),
                        timeout=10,
                    )
            except aio.TimeoutError:
                _LOGGER.exception(f'{device} not disconnected in 10 secs')
            await self._sleep_until_next_connection()

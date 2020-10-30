import asyncio as aio
import json
import logging
import os
import typing as ty

import aio_mqtt

from devices import registered_device_types
from devices.base import Device

logger = logging.getLogger(__name__)

VERSION = '0.1.0a0'


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
            loop: ty.Optional[aio.AbstractEventLoop] = None
    ) -> None:
        self._mqtt_host = host
        self._mqtt_port = port
        self._mqtt_user = user
        self._mqtt_password = password

        self._reconnection_interval = reconnection_interval
        self._loop = loop or aio.get_event_loop()
        self._client = aio_mqtt.Client(loop=self._loop)
        self._tasks = []

        self.device_registry: ty.List[Device] = []

    def start(self):
        self._tasks = [
            self._loop.create_task(self._connect_forever()),
            self._loop.create_task(self._handle_messages()),
            # self._loop.create_task(self._periodic_publish()),
            # self._loop.create_task(self._handle_buttons()),
        ]

    async def close(self) -> None:
        for task in self._tasks:
            if task.done():
                continue
            task.cancel()
            try:
                await task
            except aio.CancelledError:
                pass
        if self._client.is_connected():
            await self._client.disconnect()

    def _get_topic(self, dev_id, subtopic):
        return f'{self.TOPIC_ROOT}/{dev_id}/{subtopic}'

    def register(self, device: Device):
        if not device:
            return
        self.device_registry.append(device)

    @property
    def subscribed_topics(self):
        return [
            f'{self.TOPIC_ROOT}/{topic}'
            for device in self.device_registry
            for topic in device.subscribed_topics
        ]

    async def publish_topic_callback(self, topic, value):
        logger.debug(f'call publish callback {topic=} {value=}')
        await self._client.publish(
            aio_mqtt.PublishableMessage(
                topic_name='/'.join((self.TOPIC_ROOT, topic)),
                payload=value,
                qos=aio_mqtt.QOSLevel.QOS_1,
            ),
        )

    async def _handle_messages(self) -> None:
        async for message in self._client.delivered_messages(
            f'{self.TOPIC_ROOT}/#'
        ):
            print(message)
            while True:
                if message.topic_name not in self.subscribed_topics:
                    continue

                topic_wo_prefix = message.topic_name.removeprefix(
                    f'{self.TOPIC_ROOT}/',
                )
                for _device in self.device_registry:
                    if topic_wo_prefix in _device.subscribed_topics:
                        device = _device
                        break
                else:
                    raise NotImplementedError('Unknown topic')

                try:
                    value = json.loads(message.payload)
                except ValueError:
                    value = message.payload.decode()

                await device.process_topic(
                    topic=topic_wo_prefix,
                    value=value,
                    publish_topic=self.publish_topic_callback,
                )
                break

            await aio.sleep(1)

    async def send_config(self):
        async def start_and_send(d: Device):
            await d.init()
            device_info = {
                'identifiers': [
                    d.unique_id,
                ],
                'name': d.unique_id,
                'sw_version': d.version,
                'model': d.model,
                'manufacturer': d.manufacturer,
            }

            def get_generic_vals(entity: dict):
                result = {
                    'name': f'{entity["name"]}_{d.dev_id}',
                    'unique_id': f'{entity["name"]}_{d.dev_id}',
                    'device': device_info,
                }
                if 'icon' in entity:
                    result['icon'] = f'mdi:{entity["icon"]}'
                return result

            for cls, entities in d.entities.items():
                if cls == 'switch':
                    for entity in entities:
                        entity_name = entity['name']
                        state_topic = self._get_topic(d.unique_id, entity_name)
                        command_topic = f'{state_topic}/{d.SET_POSTFIX}'
                        config_topic = (
                            f'homeassistant/{cls}/{d.dev_id}/'
                            f'{entity_name}/config'
                        )
                        payload = json.dumps({
                            **get_generic_vals(entity),
                            'state_topic': state_topic,
                            'command_topic': command_topic,
                        })
                        logger.debug(f'Publish config {config_topic=}: {payload=}')
                        await self._client.publish(
                            aio_mqtt.PublishableMessage(
                                topic_name=config_topic,
                                payload=payload,
                                qos=aio_mqtt.QOSLevel.QOS_1,
                                retain=True,
                            ),
                        )
                        # TODO: remove test send
                        logger.debug(f'Publish initial state {state_topic=}')
                        await self._client.publish(
                            aio_mqtt.PublishableMessage(
                                topic_name=state_topic,
                                payload='OFF',
                                qos=aio_mqtt.QOSLevel.QOS_1,
                            ),
                        )

        coros = []
        for device in self.device_registry:
            coros.append(start_and_send(device))

        await aio.gather(*coros)

    async def _periodic_publish(self, period=1):
        while True:
            if not self._client.is_connected():
                await aio.sleep(1)
                continue

            await aio.sleep(period)

    async def _connect_forever(self) -> None:
        while True:
            try:
                connect_result = await self._client.connect(
                    host=self._mqtt_host,
                    port=self._mqtt_port,
                    username=self._mqtt_user,
                    password=self._mqtt_password,
                )
                logger.info("Connected")

                await self._client.subscribe(*[
                    (topic, aio_mqtt.QOSLevel.QOS_1)
                    for topic in self.subscribed_topics
                ])
                await self.send_config()

                logger.info("Wait for network interruptions...")
                await connect_result.disconnect_reason
            except aio.CancelledError:
                raise

            except aio_mqtt.AccessRefusedError as e:
                logger.error("Access refused", exc_info=e)

            except aio_mqtt.ConnectionLostError as e:
                logger.error(
                    "Connection lost. Will retry in %d seconds",
                    self._reconnection_interval,
                    exc_info=e,
                )
                await aio.sleep(self._reconnection_interval, loop=self._loop)

            except aio_mqtt.ConnectionCloseForcedError as e:
                logger.error("Connection close forced", exc_info=e)
                return

            except Exception as e:
                logger.error(
                    "Unhandled exception during connecting",
                    exc_info=e,
                )
                return

            else:
                logger.info("Disconnected")
                return


if __name__ == '__main__':
    logging.basicConfig(
        level='DEBUG'
    )
    loop = aio.new_event_loop()

    os.environ.setdefault('BLE2MQTT_CONFIG', '/etc/ble2mqtt.json')
    config = {}
    if os.path.exists(os.environ['BLE2MQTT_CONFIG']):
        try:
            with open(os.environ['BLE2MQTT_CONFIG'], 'r') as f:
                config = json.load(f)
        except Exception:
            pass

    config = {
        'mqtt_host': 'localhost',
        'mqtt_port': 1883,
        **config,
    }

    server = Ble2Mqtt(
        reconnection_interval=10,
        loop=loop,
        host=config['mqtt_host'],
        port=config['mqtt_port'],
        user=config.get('mqtt_user'),
        password=config.get('mqtt_password'),
    )

    devices = config.get('devices') or []
    for device in devices:
        try:
            mac = device.pop('address')
            typ = device.pop('type')
        except (ValueError, IndexError):
            continue
        klass = registered_device_types[typ]
        server.register(klass(
            mac=mac,
            **device,
        ))

    server.start()

    try:
        loop.run_forever()
    except KeyboardInterrupt:
        pass

    finally:
        loop.run_until_complete(server.close())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()

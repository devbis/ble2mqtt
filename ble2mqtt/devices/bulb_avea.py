import asyncio as aio
import json
import logging
import uuid

from ble2mqtt.devices.base import LIGHT_DOMAIN, ConnectionMode, Device
from ble2mqtt.devices.uuids import FIRMWARE_VERSION
from ble2mqtt.protocols.avea import AveaProtocol

AVEA_CONTROL = uuid.UUID("f815e811-456c-6761-746f-4d756e696368")

_LOGGER = logging.getLogger(__name__)

LIGHT_ENTITY = 'light'


class AveaBulb(AveaProtocol, Device):
    NAME = 'avea_rgbw'
    DATA_CHAR = AVEA_CONTROL
    ACTIVE_SLEEP_INTERVAL = 1
    RECONNECTION_SLEEP_INTERVAL = 30
    MANUFACTURER = 'Avea'
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_KEEP_CONNECTION

    SEND_DATA_PERIOD = 60

    def __init__(self, mac, *args, **kwargs):
        super().__init__(mac, *args, **kwargs)
        self._color = (255, 255, 255)  # rgb, if OFF store previous color
        self._real_color = (255, 255, 255)  # rgb
        self._brightness = 255

        self.initial_status_sent = False

    @property
    def entities(self):
        return {
            LIGHT_DOMAIN: [
                {
                    'name': 'light',
                },
            ],
        }

    async def get_device_data(self):
        await super().get_device_data()
        try:
            self._model = await self.read_name()
        except Exception as e:
            _LOGGER.warning(f'[{self}] Cannot read name: {e}')
            self._model = 'Bulb'
        version = await self._read_with_timeout(FIRMWARE_VERSION)
        if isinstance(version, (bytes, bytearray)):
            self._version = version.decode(errors='ignore').strip('\0')

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        timer = 0
        while True:
            await self.update_device_data(send_config)
            await self.read_state()

            if not self.initial_status_sent or timer >= self.SEND_DATA_PERIOD:
                await self._notify_state(publish_topic)
                timer = 0
            timer += self.ACTIVE_SLEEP_INTERVAL
            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

    async def handle_messages(self, publish_topic, *args, **kwargs):
        while True:
            try:
                if not self.client.is_connected:
                    raise ConnectionError()
                message = await aio.wait_for(
                    self.message_queue.get(),
                    timeout=60,
                )
            except aio.TimeoutError:
                await aio.sleep(1)
                continue
            value = message['value']
            entity_topic, action_postfix = self.get_entity_subtopic_from_topic(
                message['topic'],
            )
            if entity_topic == self._get_topic_for_entity(
                self.get_entity_by_name(LIGHT_DOMAIN, LIGHT_ENTITY),
                skip_unique_id=True,
            ):
                _LOGGER.info(f'[{self}] set light {value}')

                if value.get('brightness'):
                    self._brightness = value['brightness']
                    await self.write_brightness(self._brightness)

                target_color = None
                if value.get('color'):
                    color = value['color']
                    try:
                        target_color = (
                            color['r'],
                            color['g'],
                            color['b'],
                        )
                    except ValueError:
                        return
                    if target_color != (0, 0, 0):
                        self._color = target_color

                if value.get('state'):
                    state = self.transform_value(value['state'])
                    if state == 'ON' and target_color is None:
                        target_color = self._color
                    if state == 'OFF':
                        target_color = (0, 0, 0)
                if target_color is not None:
                    await self.write_color(*target_color)
                await self._notify_state(publish_topic)

    def handle_color(self, value):
        self._real_color = value
        if value != (0, 0, 0):
            self._color = value

    def handle_brightness(self, value):
        self._brightness = value

    async def _notify_state(self, publish_topic):
        _LOGGER.info(
            f'[{self}] send color={self._color}, brightness={self._brightness}',
        )
        coros = []

        state = {'linkquality': self.linkquality}
        lights = self.entities.get(LIGHT_DOMAIN, [])
        for light in lights:
            if light['name'] == LIGHT_ENTITY:
                state.update({
                    'state': (
                        'ON'
                        if (
                            self._brightness != 0 and
                            self._real_color != (0, 0, 0)
                        )
                        else 'OFF'
                    ),
                    'brightness': self._brightness,
                    'color': {
                        'r': self._color[0],
                        'g': self._color[1],
                        'b': self._color[2],
                    },
                    'color_mode': 'rgb',
                })
                coros.append(publish_topic(
                    topic=self._get_topic_for_entity(light),
                    value=json.dumps(state),
                ))
        if coros:
            await aio.gather(*coros)
            self.initial_status_sent = True

import asyncio as aio
import json
import logging
import uuid

from ..protocols.redmond import (ColorTarget, KettleG200Mode, KettleG200State,
                                 KettleRunState, RedmondKettle200Protocol)
from .base import (LIGHT_DOMAIN, SENSOR_DOMAIN, SWITCH_DOMAIN, ConnectionMode,
                   Device)
from .uuids import DEVICE_NAME

_LOGGER = logging.getLogger(__name__)

UUID_NORDIC_TX = uuid.UUID("6e400002-b5a3-f393-e0a9-e50e24dcca9e")
UUID_NORDIC_RX = uuid.UUID("6e400003-b5a3-f393-e0a9-e50e24dcca9e")

BOIL_ENTITY = 'boil'
HEAT_ENTITY = 'heat'  # not implemented yet
TEMPERATURE_ENTITY = 'temperature'
ENERGY_ENTITY = 'energy'
LIGHT_ENTITY = 'backlight'


class RedmondKettle(RedmondKettle200Protocol, Device):
    MAC_TYPE = 'random'
    NAME = 'redmond_rk_g200'
    TX_CHAR = UUID_NORDIC_TX
    RX_CHAR = UUID_NORDIC_RX
    ACTIVE_SLEEP_INTERVAL = 1
    RECONNECTION_SLEEP_INTERVAL = 30
    MANUFACTURER = 'Redmond'
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_KEEP_CONNECTION

    SEND_DATA_PERIOD = 5  # seconds when boiling
    STANDBY_SEND_DATA_PERIOD_MULTIPLIER = 12  # 12 * 5 seconds in standby mode

    def __init__(self, mac, key='ffffffffffffffff',
                 *args, **kwargs):
        super().__init__(mac, *args, **kwargs)
        assert isinstance(key, str) and len(key) == 16
        self._key = bytes.fromhex(key)
        self._state = None
        self._color = (255, 255, 255)
        self._brightness = 255
        self._statistics = {}
        self._energy = None

        self._send_data_period_multiplier = \
            self.STANDBY_SEND_DATA_PERIOD_MULTIPLIER
        self.initial_status_sent = False

    @property
    def entities(self):
        return {
            SWITCH_DOMAIN: [
                {
                    'name': BOIL_ENTITY,
                    'topic': BOIL_ENTITY,
                    'icon': 'kettle',
                },
            ],
            SENSOR_DOMAIN: [
                {
                    'name': TEMPERATURE_ENTITY,
                    'device_class': 'temperature',
                    'unit_of_measurement': '\u00b0C',
                },
                {
                    'name': ENERGY_ENTITY,
                    'device_class': 'energy',
                    'unit_of_measurement': 'Wh',
                },
                {
                    'name': 'statistics',
                    'topic': 'statistics',
                    'icon': 'chart-bar',
                    'json': True,
                    'main_value': 'number_of_starts',
                    'unit_of_measurement': ' ',
                },
            ],
            LIGHT_DOMAIN: [
                {
                    'name': LIGHT_ENTITY,
                    'topic': LIGHT_ENTITY,
                },
            ],
        }

    async def get_device_data(self):
        await self.protocol_start()
        await self.login(self._key)
        model = await self._read_with_timeout(DEVICE_NAME)
        if isinstance(model, (bytes, bytearray)):
            self._model = model.decode()
        else:
            # macos can't access characteristic
            self._model = 'G200S'
        version = await self.get_version()
        if version:
            self._version = f'{version[0]}.{version[1]}'
        state = await self.get_mode()
        if state:
            self._state = state
            self.update_multiplier()
            self.initial_status_sent = False
        await self.set_time()
        await self._update_statistics()

    def update_multiplier(self, state: KettleG200State = None):
        if state is None:
            state = self._state
        self._send_data_period_multiplier = (
            1
            if state.state == KettleRunState.ON and state.mode in [
                KettleG200Mode.BOIL,
                KettleG200Mode.HEAT,
            ]
            else self.STANDBY_SEND_DATA_PERIOD_MULTIPLIER
        )

    async def _notify_state(self, publish_topic):
        _LOGGER.info(f'[{self}] send state={self._state}')
        coros = []

        state = {'linkquality': self.linkquality}
        for sensor_name, value in (
            (TEMPERATURE_ENTITY, self._state.temperature),
            (ENERGY_ENTITY, self._energy),
        ):
            if any(
                    x['name'] == sensor_name
                    for x in self.entities.get(SENSOR_DOMAIN, [])
            ):
                state[sensor_name] = self.transform_value(value)

        if state:
            coros.append(publish_topic(
                topic=self._get_topic(self.STATE_TOPIC),
                value=json.dumps(state),
            ))

        # keep statistics in a separate topic
        _LOGGER.info(f'[{self}] send statistics={self._statistics}')
        for sensor_name, value in (
            ('statistics', self._statistics),
        ):
            entity = self.get_entity_by_name(SENSOR_DOMAIN, sensor_name)
            if entity:
                coros.append(publish_topic(
                    topic=self._get_topic_for_entity(entity),
                    value=json.dumps(value),
                ))

        lights = self.entities.get(LIGHT_DOMAIN, [])
        for light in lights:
            if light['name'] == LIGHT_ENTITY:
                light_state = {
                    'state': (
                        KettleRunState.ON.name
                        if self._state.state == KettleRunState.ON and
                        self._state.mode == KettleG200Mode.LIGHT
                        else KettleRunState.OFF.name
                    ),
                    'brightness': 255,
                    'color': {
                        'r': self._color[0],
                        'g': self._color[1],
                        'b': self._color[2],
                    },
                    'color_mode': 'rgb',
                }
                coros.append(publish_topic(
                    topic=self._get_topic_for_entity(light),
                    value=json.dumps(light_state),
                ))
        if coros:
            await aio.gather(*coros)

    async def notify_run_state(self, new_state: KettleG200State, publish_topic):
        if not self.initial_status_sent or \
                new_state.state != self._state.state or \
                new_state.mode != self._state.mode:
            state_to_str = {
                True: KettleRunState.ON.name,
                False: KettleRunState.OFF.name,
            }
            boil_mode = state_to_str[
                new_state.mode == KettleG200Mode.BOIL and
                new_state.state == KettleRunState.ON
                ]
            heat_mode = state_to_str[
                new_state.mode == KettleG200Mode.HEAT and
                new_state.state == KettleRunState.ON
                ]
            topics = {
                BOIL_ENTITY: boil_mode,
                HEAT_ENTITY: heat_mode,
            }
            await aio.gather(
                *[
                    publish_topic(topic=self._get_topic(topic), value=value)
                    for topic, value in topics.items()
                ],
                self._notify_state(publish_topic),
            )
            self.initial_status_sent = True
            if self._state != new_state:
                self._state = new_state
                await self._notify_state(publish_topic)
            else:
                self._state = new_state
        else:
            self._state = new_state
        self.update_multiplier()

    async def _update_statistics(self):
        statistics = await self.get_statistics()
        self._statistics = {
            'number_of_starts': statistics['starts'],
            'Energy spent (kWh)': round(statistics['watts_hours']/1000, 2),
            'Working time (minutes)': round(statistics['seconds_run']/60, 1),
        }
        self._energy = statistics['watts_hours']

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        counter = 0
        while True:
            await self.update_device_data(send_config)
            # if boiling notify every 5 seconds, 60 sec otherwise
            new_state = await self.get_mode()
            await self.notify_run_state(new_state, publish_topic)
            counter += 1

            if counter > (
                    self.SEND_DATA_PERIOD * self._send_data_period_multiplier
            ):
                await self._update_statistics()
                await self._notify_state(publish_topic)
                counter = 0
            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

    async def _switch_mode(self, mode, value):
        if value == KettleRunState.ON.name:
            try:
                if self._state.mode != mode:
                    await self.stop()
                await self.set_mode(KettleG200State(mode=mode))
            except ValueError:
                # if the MODE is the same then it returns
                # en error. Treat it as normal
                pass
            await self.run()
            next_state = KettleRunState.ON
        else:
            await self.stop()
            next_state = KettleRunState.OFF
        self.update_multiplier(KettleG200State(state=next_state))

    async def _switch_boil(self, value):
        await self._switch_mode(KettleG200Mode.BOIL, value)

    async def _switch_backlight(self, value):
        await self._switch_mode(KettleG200Mode.LIGHT, value)

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
            entity = self.get_entity_by_name(SWITCH_DOMAIN, BOIL_ENTITY)
            if entity_topic == self._get_topic_for_entity(
                entity,
                skip_unique_id=True,
            ):
                value = self.transform_value(value)
                _LOGGER.info(
                    f'[{self}] switch kettle {BOIL_ENTITY} value={value}',
                )
                while True:
                    try:
                        await self._switch_boil(value)
                        # update state to real values
                        await self.get_mode()
                        await aio.gather(
                            publish_topic(
                                topic=self._get_topic_for_entity(entity),
                                value=self.transform_value(value),
                            ),
                            self._notify_state(publish_topic),
                            loop=self._loop,
                        )
                        break
                    except ConnectionError as e:
                        _LOGGER.exception(str(e))
                    await aio.sleep(5)
                continue

            entity = self.get_entity_by_name(LIGHT_DOMAIN, LIGHT_ENTITY)
            if entity_topic == self._get_topic_for_entity(
                entity,
                skip_unique_id=True,
            ):
                _LOGGER.info(f'set backlight {value}')
                if value.get('state'):
                    await self._switch_backlight(value['state'])
                if value.get('color') or value.get('brightness'):
                    if value.get('color'):
                        color = value['color']
                        try:
                            self._color = color['r'], color['g'], color['b']
                        except ValueError:
                            return
                    if value.get('brightness'):
                        self._brightness = value['brightness']
                    await aio.gather(
                        self.set_color(
                            ColorTarget.LIGHT,
                            *self._color,
                            self._brightness,
                        ),
                        self._notify_state(publish_topic),
                        loop=self._loop,
                    )

import asyncio as aio
import json
import logging
import typing as ty
import uuid

from ..protocols.redmond import (ColorTarget, Kettle200State, Mode,
                                 RedmondKettle200Protocol, RunState)
from .base import LIGHT_DOMAIN, SENSOR_DOMAIN, SWITCH_DOMAIN, Device
from .uuids import DEVICE_NAME

logger = logging.getLogger(__name__)

UUID_NORDIC_TX = uuid.UUID("6e400002-b5a3-f393-e0a9-e50e24dcca9e")
UUID_NORDIC_RX = uuid.UUID("6e400003-b5a3-f393-e0a9-e50e24dcca9e")

BOIL_ENTITY = 'boil'
HEAT_ENTITY = 'heat'  # not implemented yet
TEMPERATURE_ENTITY = 'temperature'
LIGHT_ENTITY = 'backlight'


class RedmondKettle(RedmondKettle200Protocol, Device):
    MAC_TYPE = 'random'
    NAME = 'redmond200'
    TX_CHAR = UUID_NORDIC_TX
    RX_CHAR = UUID_NORDIC_RX
    CONNECTION_TIMEOUT = 30
    MANUFACTURER = 'Redmond'

    UPDATE_PERIOD = 5  # seconds when boiling
    STANDBY_UPDATE_PERIOD_MULTIPLIER = 12  # 12 * 5 seconds in standby mode

    def __init__(self, mac, key=b'\xff\xff\xff\xff\xff\xff\xff\xff',
                 *args, loop, **kwargs):
        super().__init__(mac, *args, loop=loop, **kwargs)
        assert isinstance(key, bytes) and len(key) == 8
        self._key = key
        self._state = None
        self._color = (255, 255, 255)
        self._brightness = 255
        self._statistics = {}

        self._update_period_multiplier = self.STANDBY_UPDATE_PERIOD_MULTIPLIER
        self.initial_status_sent = False

    @property
    def entities(self):
        return {
            SWITCH_DOMAIN: [
                {
                    'name': BOIL_ENTITY,
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
                },
            ],
        }

    async def get_device_data(self) -> ty.Sequence[aio.Future]:
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
        return [self.queue_handler] if self.queue_handler else []

    def update_multiplier(self, state: Kettle200State = None):
        if state is None:
            state = self._state
        self._update_period_multiplier = (
            1
            if state.state == RunState.ON and
            state.mode in [Mode.BOIL, Mode.HEAT]
            else self.STANDBY_UPDATE_PERIOD_MULTIPLIER
        )

    async def _notify_state(self, publish_topic):
        logger.info(f'[{self._mac}] send state={self._state}')
        coros = []

        state = {}
        for sensor_name, value in (
            ('temperature', self._state.temperature),
        ):
            if any(
                    x['name'] == sensor_name
                    for x in self.entities.get('sensor', [])
            ):
                state[sensor_name] = self.transform_value(value)

        if state:
            coros.append(publish_topic(
                topic='/'.join((self.unique_id, 'state')),
                value=json.dumps(state),
            ))

        # keep statistics in a separate topic
        logger.info(f'[{self._mac}] send statistics={self._statistics}')
        for sensor_name, value in (
            ('statistics', self._statistics),
        ):
            if any(
                x['name'] == sensor_name
                for x in self.entities.get('sensor', [])
            ):
                coros.append(publish_topic(
                    topic='/'.join((self.unique_id, sensor_name)),
                    value=json.dumps(value),
                ))

        lights = self.entities.get('light', [])
        for light in lights:
            if light['name'] == LIGHT_ENTITY:
                light_state = {
                    'state': (
                        RunState.ON.name
                        if self._state.state == RunState.ON and
                        self._state.mode == Mode.LIGHT
                        else RunState.OFF.name
                    ),
                    'brightness': 255,
                    'color': {
                        'r': self._color[0],
                        'g': self._color[1],
                        'b': self._color[2],
                    },
                }
                coros.append(publish_topic(
                    topic='/'.join((self.unique_id, light['name'])),
                    value=json.dumps(light_state),
                ))
        if coros:
            await aio.gather(*coros)

    async def notify_run_state(self, new_state: Kettle200State, publish_topic):
        if not self.initial_status_sent or \
                new_state.state != self._state.state or \
                new_state.mode != self._state.mode:
            state_to_str = {
                True: RunState.ON.name,
                False: RunState.OFF.name,
            }
            boil_mode = state_to_str[
                new_state.mode == Mode.BOIL and
                new_state.state == RunState.ON
            ]
            heat_mode = state_to_str[
                new_state.mode == Mode.HEAT and
                new_state.state == RunState.ON
            ]
            topics = {
                BOIL_ENTITY: boil_mode,
                HEAT_ENTITY: heat_mode,
            }
            await aio.gather(
                *[
                    publish_topic(
                        topic='/'.join((self.unique_id, topic)),
                        value=value,
                    ) for topic, value in topics.items()
                ],
                self._notify_state(publish_topic),
            )
            self.initial_status_sent = True
            self._state = new_state
            await self._notify_state(publish_topic)
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

    async def handle(self, publish_topic, *args, **kwargs):
        counter = 0
        while True:
            # if boiling notify every 5 seconds, 60 sec otherwise
            new_state = await self.get_mode()
            await self.notify_run_state(new_state, publish_topic)
            counter += 1

            if counter > self.UPDATE_PERIOD * self._update_period_multiplier:
                await self._update_statistics()
                await self._notify_state(publish_topic)
                counter = 0
            await aio.sleep(1)

    async def _switch_mode(self, mode, value):
        if value == RunState.ON.name:
            try:
                if self._state.mode != mode:
                    await self.stop()
                await self.set_mode(Kettle200State(mode=mode))
            except ValueError:
                # if the MODE is the same then it returns
                # en error. Treat it as normal
                pass
            await self.run()
            next_state = RunState.ON
        else:
            await self.stop()
            next_state = RunState.OFF
        self.update_multiplier(Kettle200State(state=next_state))

    async def _switch_boil(self, value):
        await self._switch_mode(Mode.BOIL, value)

    async def _switch_backlight(self, value):
        await self._switch_mode(Mode.LIGHT, value)

    async def handle_messages(self, publish_topic, *args, **kwargs):
        while True:
            message = await self.message_queue.get()
            value = message['value']
            entity_name = self.get_entity_from_topic(message['topic'])
            if entity_name == BOIL_ENTITY:
                value = self.transform_value(value)
                logger.info(
                    f'[{self._mac}] switch kettle {entity_name} value={value}',
                )
                while True:
                    try:
                        await self._switch_boil(value)
                        # update state to real values
                        await self.get_mode()
                        await aio.gather(
                            publish_topic(
                                topic='/'.join((self.unique_id, entity_name)),
                                value=self.transform_value(value),
                            ),
                            self._notify_state(publish_topic),
                            loop=self._loop,
                        )
                        break
                    except ConnectionError as e:
                        logger.exception(str(e))
                    await aio.sleep(5)
            if entity_name == LIGHT_ENTITY:
                logger.info(f'set backlight {value}')
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
                    await self.set_color(
                        ColorTarget.LIGHT,
                        *self._color,
                        self._brightness,
                    )

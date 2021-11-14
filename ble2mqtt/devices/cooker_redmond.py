import asyncio as aio
import json
import logging
import uuid
from contextlib import asynccontextmanager

from ..protocols.redmond import (COOKER_PREDEFINED_PROGRAMS, CookerRunState,
                                 CookerState, RedmondCookerProtocol,
                                 RedmondError)
from .base import (SELECT_DOMAIN, SENSOR_DOMAIN, SWITCH_DOMAIN, ConnectionMode,
                   Device)
from .uuids import DEVICE_NAME

_LOGGER = logging.getLogger(__name__)

UUID_NORDIC_TX = uuid.UUID("6e400002-b5a3-f393-e0a9-e50e24dcca9e")
UUID_NORDIC_RX = uuid.UUID("6e400003-b5a3-f393-e0a9-e50e24dcca9e")

COOK_ENTITY = 'cook'
LOCK_ENTITY = 'lock'
SOUND_ENTITY = 'sound'
PREDEFINED_PROGRAM_ENTITY = 'predefined_program'
TEMPERATURE_ENTITY = 'temperature'
MODE_ENTITY = 'mode'


def option_to_const(option):
    return option.replace(' ', '_').lower()


def const_to_option(const):
    return const.replace('_', ' ').title()


class RedmondCooker(RedmondCookerProtocol, Device):
    MAC_TYPE = 'random'
    NAME = 'redmond_rmc_m200'
    TX_CHAR = UUID_NORDIC_TX
    RX_CHAR = UUID_NORDIC_RX
    ACTIVE_SLEEP_INTERVAL = 1
    RECONNECTION_SLEEP_INTERVAL = 30
    MANUFACTURER = 'Redmond'
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_KEEP_CONNECTION

    SEND_DATA_PERIOD = 5  # seconds when boiling
    STANDBY_SEND_DATA_PERIOD_MULTIPLIER = 12  # 12 * 5 seconds in standby mode

    def __init__(self, mac, key='ffffffffffffffff', default_program='express',
                 *args, loop, **kwargs):
        super().__init__(mac, *args, loop=loop, **kwargs)
        assert isinstance(key, str) and len(key) == 16
        self._default_program = default_program
        self._key = bytes.fromhex(key)
        self._state: CookerState = None

        self._send_data_period_multiplier = \
            self.STANDBY_SEND_DATA_PERIOD_MULTIPLIER
        self.initial_status_sent = False

    @property
    def entities(self):
        return {
            SWITCH_DOMAIN: [
                {
                    'name': COOK_ENTITY,
                    'topic': COOK_ENTITY,
                    'icon': 'pot-steam',
                },
                {
                    'name': LOCK_ENTITY,
                    'topic': LOCK_ENTITY,
                    'icon': 'lock',
                },
                {
                    'name': SOUND_ENTITY,
                    'topic': SOUND_ENTITY,
                    'icon': 'music-note',
                },
            ],
            SENSOR_DOMAIN: [
                {
                    'name': TEMPERATURE_ENTITY,
                    'device_class': 'temperature',
                    'unit_of_measurement': '\u00b0C',
                },
                {
                    'name': MODE_ENTITY,
                },
            ],
            SELECT_DOMAIN: [
                {
                    'name': PREDEFINED_PROGRAM_ENTITY,
                    'topic': PREDEFINED_PROGRAM_ENTITY,
                    'options': [
                        const_to_option(x)
                        for x in COOKER_PREDEFINED_PROGRAMS.keys()
                    ],
                },
            ],
        }

    async def update_device_state(self):
        state = await self.get_mode()
        if state:
            self._state = state
            self.update_multiplier()
            self.initial_status_sent = False

    async def get_device_data(self):
        await self.protocol_start()
        await self.login(self._key)
        model = await self._read_with_timeout(DEVICE_NAME)
        if isinstance(model, (bytes, bytearray)):
            self._model = model.decode()
        else:
            # macos can't access characteristic
            self._model = 'RMC'
        version = await self.get_version()
        if version:
            self._version = f'{version[0]}.{version[1]}'
        await self.update_device_state()
        # await self.set_time()
        # await self._update_statistics()

    def update_multiplier(self, state: CookerState = None):
        if state is None:
            state = self._state
        self._send_data_period_multiplier = (
            1
            if state.state in [
                CookerRunState.HEAT,
                CookerRunState.SETUP_PROGRAM,
                CookerRunState.WARM_UP,
                CookerRunState.COOKING,
            ]
            else self.STANDBY_SEND_DATA_PERIOD_MULTIPLIER
        )

    async def _notify_state(self, publish_topic):
        _LOGGER.info(f'[{self}] send state={self._state}')
        coros = []

        state = {'linkquality': self.linkquality}
        for sensor_name, value in (
            (TEMPERATURE_ENTITY, self._state.target_temperature),
            (MODE_ENTITY, self._state.state.name.title().replace('_', ' ')),
        ):
            if any(
                    x['name'] == sensor_name
                    for x in self.entities.get(SENSOR_DOMAIN, [])
            ):
                state[sensor_name] = value  # no need to transform

        if state:
            coros.append(publish_topic(
                topic=self._get_topic(self.STATE_TOPIC),
                value=json.dumps(state),
            ))

        selects = self.entities.get(SELECT_DOMAIN, [])
        for select in selects:
            if select['name'] == PREDEFINED_PROGRAM_ENTITY:
                back_programs = {
                    (state.program, state.subprogram): k
                    for k, state in COOKER_PREDEFINED_PROGRAMS.items()
                }
                coros.append(publish_topic(
                    topic=self._get_topic_for_entity(select),
                    value=const_to_option(back_programs.get(
                        (self._state.program, self._state.subprogram),
                        '',
                    )),
                ))
        if coros:
            await aio.gather(*coros)

    async def notify_run_state(self, new_state: CookerState, publish_topic):
        if not self.initial_status_sent or \
                new_state.state != self._state.state:
            state_to_str = {
                True: 'ON',
                False: 'OFF',
            }
            mode = state_to_str[new_state.state not in (
                CookerRunState.OFF,
                CookerRunState.SETUP_PROGRAM,
            )]
            await aio.gather(
                publish_topic(topic=self._get_topic(COOK_ENTITY), value=mode),
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
                # await self._update_statistics()
                await self._notify_state(publish_topic)
                counter = 0
            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

    async def switch_running_mode(self, value):
        if value == 'ON':
            # switch to SETUP_PROGRAM mode if it is off
            if self._state.state == CookerRunState.OFF:
                try:
                    await self.set_predefined_program(self._default_program)
                except KeyError:
                    # if incorrect program passed, use initial mode
                    await self.set_mode(self._state)
            await self.run()
            next_state = CookerRunState.COOKING  # any of heating stages
        else:
            await self.stop()
            next_state = CookerRunState.OFF
        self.update_multiplier(CookerState(state=next_state))

    async def handle_messages(self, publish_topic, *args, **kwargs):
        def is_entity_topic(entity, topic):
            return topic == self._get_topic_for_entity(
                entity,
                skip_unique_id=True,
            )

        @asynccontextmanager
        async def process_entity_change(entity, value):
            value = self.transform_value(value)
            _LOGGER.info(
                f'[{self}] switch cooker {entity["name"]} value={value}',
            )
            for _ in range(10):
                try:
                    yield
                    # update state to real values
                    await self.update_device_state()
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
            entity = self.get_entity_by_name(SWITCH_DOMAIN, COOK_ENTITY)
            if is_entity_topic(entity, entity_topic):
                async with process_entity_change(entity, value):
                    try:
                        await self.switch_running_mode(value)
                    except RedmondError:
                        _LOGGER.exception(
                            f'[{self}] Problem with switching cooker',
                        )
                continue

            entity = self.get_entity_by_name(SWITCH_DOMAIN, SOUND_ENTITY)
            if is_entity_topic(entity, entity_topic):
                async with process_entity_change(entity, value):
                    await self.set_sound(value == 'ON')
                continue

            entity = self.get_entity_by_name(SWITCH_DOMAIN, LOCK_ENTITY)
            if is_entity_topic(entity, entity_topic):
                async with process_entity_change(entity, value):
                    await self.set_lock(value == 'ON')
                continue

            entity = self.get_entity_by_name(
                SELECT_DOMAIN,
                PREDEFINED_PROGRAM_ENTITY,
            )
            if is_entity_topic(entity, entity_topic):
                try:
                    value = option_to_const(value)
                except KeyError:
                    _LOGGER.error(f'{self} program "{value}" does not exist')
                    continue
                _LOGGER.info(f'set predefined program {value}')

                while True:
                    try:
                        await self.set_predefined_program(value)
                        # update state to real values
                        await self.update_device_state()
                        await aio.gather(
                            publish_topic(
                                topic=self._get_topic_for_entity(entity),
                                value=const_to_option(
                                    self.transform_value(value),
                                ),
                            ),
                            self._notify_state(publish_topic),
                            loop=self._loop,
                        )
                        break
                    except ConnectionError as e:
                        _LOGGER.exception(str(e))
                    await aio.sleep(5)
                continue

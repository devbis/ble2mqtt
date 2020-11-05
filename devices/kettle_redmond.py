import asyncio as aio
import json
import logging
import uuid

from bleak import BleakClient

from protocols.redmond import (Kettle200State, Mode, RedmondKettle200Protocol,
                               RunState)

from .base import Device

logger = logging.getLogger(__name__)

UUID_NORDIC_TX = uuid.UUID("6e400002-b5a3-f393-e0a9-e50e24dcca9e")
UUID_NORDIC_RX = uuid.UUID("6e400003-b5a3-f393-e0a9-e50e24dcca9e")


class RedmondKettle(RedmondKettle200Protocol, Device):
    NAME = 'redmond200'
    TX_CHAR = UUID_NORDIC_TX
    RX_CHAR = UUID_NORDIC_RX
    REQUIRE_CONNECTION = True

    UPDATE_PERIOD = 5  # seconds when boiling
    STANDBY_UPDATE_PERIOD_MULTIPLIER = 12  # 15 * 5 seconds in standby mode

    def __init__(self, loop, mac, model,
                 key=b'\xff\xff\xff\xff\xff\xff\xff\xff', *args, **kwargs):
        super().__init__(loop, *args, **kwargs)
        assert isinstance(key, bytes) and len(key) == 8
        self._mac = mac
        self._key = key
        self._model = model
        self._version = None
        self._state = None
        self._update_period_multiplier = self.STANDBY_UPDATE_PERIOD_MULTIPLIER
        self.initial_status_sent = False

        self.client = BleakClient(mac, address_type='random')
        self.protocol_init(client=self.client)

    @property
    def dev_id(self):
        return self._mac.replace(':', '').lower()

    @property
    def manufacturer(self):
        return 'Redmond'

    @property
    def entities(self):
        return {
            'switch': [
                {
                    'name': 'kettle',
                    'icon': 'kettle',
                },
            ],
            'sensor': [
                {
                    'name': 'temperature',
                    'device_class': 'temperature',
                    'unit_of_measurement': '\u00b0C',
                },
            ],
        }

    async def init(self):
        await super().protocol_start()
        await self.login(self._key)
        version = await self.get_version()
        if version:
            self._version = f'{version[0]}.{version[1]}'
        state = await self.get_mode()
        if state:
            self._state = state
            self.update_multiplier()
            self.initial_status_sent = False
        await self.set_time()

    def update_multiplier(self, state: Kettle200State = None):
        if state is None:
            state = self._state
        self._update_period_multiplier = (
            1 if state.state == RunState.ON
            else self.STANDBY_UPDATE_PERIOD_MULTIPLIER
        )

    async def _notify_state(self, publish_topic):
        logger.info(f'[{self._mac}] send state {self._state=}')
        state_temperature = 'temperature'
        if any(
                x['name'] == state_temperature
                for x in self.entities.get('sensor', [])
        ):
            await publish_topic(
                topic='/'.join((self.unique_id, 'temperature')),
                value=json.dumps({
                    state_temperature:
                        self.transform_value(self._state.temperature)
                }),
            )

    async def handle(self, publish_topic, *args, **kwargs):
        counter = 0
        while True:
            # if boiling notify every 5 seconds, 60 sec otherwise
            try:
                new_state = await self.get_mode()
                if new_state.state != self._state.state or \
                        not self.initial_status_sent:
                    await publish_topic(
                        topic='/'.join((self.unique_id, 'kettle')),
                        value=new_state.state.name,
                    )
                    self.initial_status_sent = True
                    self._state = new_state
                    await self._notify_state(publish_topic)
                else:
                    self._state = new_state
                self.update_multiplier()
            except ConnectionError as e:
                logger.exception(str(e))
                await aio.sleep(10)
                continue
            counter += 1

            if counter > self.UPDATE_PERIOD * self._update_period_multiplier:
                await self._notify_state(publish_topic)
                counter = 0
            await aio.sleep(1)

    async def _switch_kettle(self, value):
        if value == RunState.ON.name:
            try:
                await self.set_mode(Kettle200State(
                    mode=Mode.BOIL,
                ))
            except ValueError:
                # if the MODE is already BOIL then it returns
                # en error. Treat it as normal
                pass
            await self.run()
            next_state = RunState.ON
        else:
            await self.stop()
            next_state = RunState.OFF
        self.update_multiplier(Kettle200State(state=next_state))

    async def handle_messages(self, publish_topic, *args, **kwargs):
        while True:
            message = await self.message_queue.get()
            value = message['value']
            entity_name = self.get_entity_from_topic(message['topic'])
            if entity_name == 'kettle':
                value = self.transform_value(value)
                logger.info(f'[{self._mac}] switch kettle {entity_name=} {value=}')
                while True:
                    try:
                        await self._switch_kettle(value)
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

    def on_disconnect(self, client, *args):
        super().on_disconnect(client, *args)
        # TODO: stop notification on disconnect
        # self._loop.create_task(self.client.stop_notify(self.RX_CHAR))

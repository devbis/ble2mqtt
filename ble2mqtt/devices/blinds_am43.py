import asyncio as aio
import json
import logging
import typing as ty
import uuid
from dataclasses import dataclass
from enum import Enum

from ..protocols.am43 import AM43Protocol
from .base import (COVER_DOMAIN, SENSOR_DOMAIN, ConnectionMode, CoverRunState,
                   Device)

_LOGGER = logging.getLogger(__name__)

COVER_ENTITY = 'cover'

BLINDS_CONTROL = uuid.UUID("0000fe51-0000-1000-8000-00805f9b34fb")


class MovementType(Enum):
    STOP = 0
    POSITION = 1


@dataclass
class AM43State:
    battery: ty.Optional[int] = None
    position: int = 0
    illuminance: int = 0
    run_state: CoverRunState = CoverRunState.CLOSED
    target_position: ty.Optional[int] = None


class AM43Cover(AM43Protocol, Device):
    NAME = 'am43'
    MANUFACTURER = 'Blind'
    DATA_CHAR = BLINDS_CONTROL
    ACTIVE_SLEEP_INTERVAL = 1
    SEND_DATA_PERIOD = 5
    STANDBY_SEND_DATA_PERIOD_MULTIPLIER = 12 * 5  # 5 minutes
    LINKQUALITY_TOPIC = COVER_ENTITY
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_KEEP_CONNECTION

    # HA notation. We convert value on setting and receiving data
    CLOSED_POSITION = 0
    OPEN_POSITION = 100

    @property
    def entities(self):
        return {
            COVER_DOMAIN: [
                {
                    'name': COVER_ENTITY,
                    'topic': COVER_ENTITY,
                    'device_class': 'shade',
                },
            ],
            SENSOR_DOMAIN: [
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                },
            ],
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._model = 'AM43'
        self._state = AM43State()
        self.initial_status_sent = False

    async def get_device_data(self):
        await super().get_device_data()
        await self.client.start_notify(
            self.DATA_CHAR,
            self.notification_callback,
        )
        await self._get_full_state()

    async def _notify_state(self, publish_topic):
        _LOGGER.info(f'[{self}] send state={self._state}')
        coros = []

        state = {'linkquality': self.linkquality}
        covers = self.entities.get(COVER_DOMAIN, [])
        for cover in covers:
            if cover['name'] == COVER_ENTITY:
                cover_state = {
                    **state,
                    'state': self._state.run_state.value,
                    'position': self._state.position,
                    'battery': self._state.battery,
                    'illuminance': self._state.illuminance,
                }
                coros.append(publish_topic(
                    topic=self._get_topic_for_entity(cover),
                    value=json.dumps(cover_state),
                ))
        if coros:
            await aio.gather(*coros)
            self.initial_status_sent = True

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        # request every SEND_DATA_PERIOD if running and
        # SEND_DATA_PERIOD * STANDBY_SEND_DATA_PERIOD_MULTIPLIER if in
        # standby mode

        timer = 0
        while True:
            await self.update_device_data(send_config)
            # if running notify every 5 seconds, 60 sec otherwise
            is_running = self._state.run_state in [
                CoverRunState.OPENING,
                CoverRunState.CLOSING,
            ]
            multiplier = (
                1 if is_running else self.STANDBY_SEND_DATA_PERIOD_MULTIPLIER
            )

            timer += self.ACTIVE_SLEEP_INTERVAL
            if not self.initial_status_sent or \
                    timer >= self.SEND_DATA_PERIOD * multiplier:
                if is_running:
                    _LOGGER.debug(f'[{self}] check for position')
                    await self._get_position()
                    if self._state.position == self.CLOSED_POSITION:
                        _LOGGER.info(
                            f'[{self}] Minimum position reached. Set to CLOSED',
                        )
                        self._state.run_state = CoverRunState.CLOSED
                    elif self._state.position == self.OPEN_POSITION:
                        _LOGGER.info(
                            f'[{self}] Maximum position reached. Set to OPEN',
                        )
                        self._state.run_state = CoverRunState.OPEN
                else:
                    _LOGGER.debug(f'[{self}] check for full state')
                    await self._get_full_state()
                await self._notify_state(publish_topic)
                timer = 0
            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

    def handle_battery(self, value):
        self._state.battery = value

    def handle_position(self, value):
        self._state.position = value

    def handle_illuminance(self, value):
        self._state.illuminance = value

    async def _do_movement(self, movement_type: MovementType, target_position):
        if movement_type == MovementType.POSITION and \
                target_position is not None:
            if self.CLOSED_POSITION <= target_position <= self.OPEN_POSITION:
                await self._set_position(target_position)
                if self._state.position > target_position:
                    self._state.target_position = target_position
                    self._state.run_state = CoverRunState.CLOSING
                elif self._state.position < target_position:
                    self._state.target_position = target_position
                    self._state.run_state = CoverRunState.OPENING
                else:
                    self._state.target_position = None
                    if target_position == self.OPEN_POSITION:
                        self._state.run_state = CoverRunState.OPEN
                    elif target_position == self.CLOSED_POSITION:
                        self._state.run_state = CoverRunState.CLOSED
                    else:
                        self._state.run_state = CoverRunState.STOPPED
            else:
                _LOGGER.error(
                    f'[{self}] Incorrect position value: '
                    f'{repr(target_position)}',
                )
        else:
            await self._stop()
            self._state.run_state = CoverRunState.STOPPED

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
                self.get_entity_by_name(COVER_DOMAIN, COVER_ENTITY),
                skip_unique_id=True,
            ):
                value = self.transform_value(value)
                target_position = None
                if action_postfix == self.SET_POSTFIX:
                    _LOGGER.info(
                        f'[{self}] set mode {entity_topic} to "{value}"',
                    )
                    if value.lower() == 'open':
                        movement_type = MovementType.POSITION
                        target_position = self.OPEN_POSITION
                    elif value.lower() == 'close':
                        movement_type = MovementType.POSITION
                        target_position = self.CLOSED_POSITION
                    else:
                        movement_type = MovementType.STOP
                elif action_postfix == self.SET_POSITION_POSTFIX:
                    movement_type = MovementType.POSITION
                    _LOGGER.info(
                        f'[{self}] set position {entity_topic} to "{value}"',
                    )
                    try:
                        target_position = int(value)
                    except ValueError:
                        pass
                else:
                    _LOGGER.warning(
                        f'[{self}] unknows action postfix {action_postfix}',
                    )
                    continue

                while True:
                    try:
                        await self._do_movement(movement_type, target_position)
                        await self._notify_state(publish_topic)
                        break
                    except ConnectionError as e:
                        _LOGGER.exception(str(e))
                    await aio.sleep(5)

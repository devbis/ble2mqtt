import asyncio as aio
import json
import logging
import uuid
from dataclasses import dataclass
from enum import Enum

from ..protocols.am43 import AM43Protocol
from .base import (COVER_DOMAIN, SENSOR_DOMAIN, ActiveDeviceHandler,
                   CoverRunState, Device, SupportOnDemandCommand)

logger = logging.getLogger(__name__)

COVER_ENTITY = 'cover'

BLINDS_CONTROL = uuid.UUID("0000fe51-0000-1000-8000-00805f9b34fb")


class MovementType(Enum):
    STOP = 0
    POSITION = 1


@dataclass
class AM43State:
    battery: int = None
    position: int = 0
    illuminance: int = 0
    run_state: CoverRunState = CoverRunState.CLOSED
    target_position: int = None


# MRO makes sense
class AM43Cover(SupportOnDemandCommand, AM43Protocol, Device):
    NAME = 'am43'
    MANUFACTURER = 'Blind'
    DATA_CHAR = BLINDS_CONTROL
    ACTIVE_SLEEP_INTERVAL = 1
    SEND_DATA_PERIOD = 5
    STANDBY_SEND_DATA_PERIOD_MULTIPLIER = 12 * 5  # 5 minutes

    ON_DEMAND_CONNECTION = True
    ON_DEMAND_POLL_TIME = 15 * 60  # 15 minutes

    # HA notation. We convert value on setting and receiving data
    CLOSED_POSITION = 0
    OPEN_POSITION = 100

    @property
    def entities(self):
        return {
            COVER_DOMAIN: [
                {
                    'name': COVER_ENTITY,
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

    def __init__(self, pin=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._model = 'AM43'
        self._state = AM43State()
        self._pin = pin
        self.initial_status_sent = False

    async def on_each_connection(self):
        await super().on_each_connection()
        await self.client.start_notify(
            self.DATA_CHAR,
            self.notification_callback,
        )
        if self._pin is not None:
            await self.login(self._pin)
        await self._get_full_state()

    async def _notify_state(self, publish_topic):
        logger.info(f'[{self}] send state={self._state}')
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

    async def handle_loop(self, publish_topic, handler: ActiveDeviceHandler,
                          *args, **kwargs):
        # works while disconnected too

        # request every SEND_DATA_PERIOD if running and
        # SEND_DATA_PERIOD * STANDBY_SEND_DATA_PERIOD_MULTIPLIER if in
        # standby mode
        is_running = self._state.run_state in [
            CoverRunState.OPENING,
            CoverRunState.CLOSING,
        ]
        multiplier = (
            1 if is_running else self.STANDBY_SEND_DATA_PERIOD_MULTIPLIER
        )
        if not self.initial_status_sent or \
                handler.timer >= self.SEND_DATA_PERIOD * multiplier:
            if is_running:
                logger.debug(f'[{self}] check for position')
                await self._get_position()
                if self._state.position == self.CLOSED_POSITION:
                    logger.info(
                        f'[{self}] Minimum position reached. '
                        f'Set to CLOSED',
                    )
                    self._state.run_state = CoverRunState.CLOSED
                elif self._state.position == self.OPEN_POSITION:
                    logger.info(
                        f'[{self}] Maximum position reached. '
                        f'Set to OPEN',
                    )
                    self._state.run_state = CoverRunState.OPEN
            else:
                logger.debug(f'[{self}] check for full state')
                await self._get_full_state()
            await self._notify_state(publish_topic)
            self.initial_status_sent = True
            self.can_disconnect.set()
            handler.reset_timer()

    def handle_login(self, value):
        if not value:
            logger.error(f'[{self}] incorrect pin')

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
                logger.error(
                    f'[{self}] Incorrect position value: '
                    f'{repr(target_position)}',
                )
        else:
            await self._stop()
            self._state.run_state = CoverRunState.STOPPED

    async def handle_messages(self, publish_topic, *args, **kwargs):
        while True:
            message = await self.wait_for_mqtt_message()
            if message is None:
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
                    logger.info(
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
                    logger.info(
                        f'[{self}] set position {entity_topic} to "{value}"',
                    )
                    try:
                        target_position = int(value)
                    except ValueError:
                        pass
                else:
                    logger.warning(
                        f'[{self}] unknows action postfix {action_postfix}',
                    )
                    continue

                while True:
                    try:
                        await self._do_movement(movement_type, target_position)
                        await self._notify_state(publish_topic)
                        break
                    except ConnectionError as e:
                        logger.exception(str(e))
                    await aio.sleep(5)

                await self.init_disconnect_timer()

import asyncio as aio
import json
import logging
import typing as ty
import uuid
from dataclasses import dataclass
from enum import Enum

from ..protocols.soma import MotorCommandCodes, SomaProtocol
from .base import (COVER_DOMAIN, SENSOR_DOMAIN, ConnectionMode, CoverRunState,
                   Device)
from .uuids import BATTERY, SOFTWARE_VERSION

_LOGGER = logging.getLogger(__name__)

COVER_ENTITY = 'cover'
BATTERY_ENTITY = 'battery'
ILLUMINANCE_ENTITY = 'illuminance'

POSITION_UUID = uuid.UUID('00001525-b87f-490c-92cb-11ba5ea5167c')
MOVE_PERCENT_UUID = uuid.UUID('00001526-b87f-490c-92cb-11ba5ea5167c')
MOTOR_UUID = uuid.UUID('00001530-b87f-490c-92cb-11ba5ea5167c')
NOTIFY_UUID = uuid.UUID('00001531-b87f-490c-92cb-11ba5ea5167c')
GROUP_UUID = uuid.UUID('00001893-b87f-490c-92cb-11ba5ea5167c')
NAME_UUID = uuid.UUID('00001892-b87f-490c-92cb-11ba5ea5167c')
CALIBRATE_UUID = uuid.UUID('00001529-b87f-490c-92cb-11ba5ea5167c')
STATE_UUID = uuid.UUID('00001894-b87f-490c-92cb-11ba5ea5167c')
CONFIG_UUID = uuid.UUID('00001896-b87f-490c-92cb-11ba5ea5167c')


class MovementType(Enum):
    STOP = 0
    POSITION = 1


@dataclass
class SomaState:
    battery: ty.Optional[int] = None
    position: int = 0
    illuminance: int = 0
    motor_speed: int = 0
    run_state: CoverRunState = CoverRunState.CLOSED
    target_position: ty.Optional[int] = None


class SomaCover(SomaProtocol, Device):
    NAME = 'soma_shades'
    MANUFACTURER = 'Soma'

    NAME_CHAR = NAME_UUID
    POSITION_CHAR = POSITION_UUID
    MOTOR_CHAR = MOTOR_UUID
    SET_POSITION_CHAR = MOVE_PERCENT_UUID
    CHARGING_CHAR = STATE_UUID
    BATTERY_CHAR = BATTERY
    CONFIG_CHAR = CONFIG_UUID

    ACTIVE_SLEEP_INTERVAL = 1
    SEND_DATA_PERIOD = 5
    STANDBY_SEND_DATA_PERIOD_MULTIPLIER = 12 * 5  # 5 minutes
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
                    'name': BATTERY_ENTITY,
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                },
                {
                    'name': ILLUMINANCE_ENTITY,
                    'device_class': 'illuminance',
                    'unit_of_measurement': 'lx',
                },
            ],
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self._model = 'AM43'
        self._state = SomaState()
        self.initial_status_sent = False

    async def get_device_data(self):
        await super().get_device_data()
        name = await self._read_with_timeout(self.NAME_CHAR)
        if isinstance(name, (bytes, bytearray)):
            self._model = name.decode().strip(' \0')
        version = await self.client.read_gatt_char(SOFTWARE_VERSION)
        if version:
            self._version = version.decode()
        _LOGGER.debug(f'{self} name: {name}, version: {version}')

        cb = self.notification_callback
        await self.client.start_notify(self.CONFIG_CHAR, cb)
        await self.client.start_notify(self.POSITION_CHAR, cb)
        await self.client.start_notify(self.CHARGING_CHAR, cb)
        await self._get_full_state()

    def _handle_position(self, value):
        self._state.position = value

    def _handle_charging(self, *, charging_level, panel_level):
        self._state.illuminance = charging_level

    def _handle_motor_run_state(self, run_state: MotorCommandCodes):
        # Ignore run state.
        # We calculate run state on position and target position
        pass

    async def _get_full_state(self):
        self._state.battery = await self._get_battery()
        self._state.target_position = await self._get_target_position()
        self._state.position = await self._get_position()
        self._state.illuminance = \
            (await self._get_light_and_panel())['charging_level']
        self._state.run_state = self._get_run_state()
        self._state.motor_speed = await self._get_motor_speed()

    def _get_run_state(self) -> CoverRunState:
        if self._state.target_position == self._state.position == \
                self.OPEN_POSITION:
            return CoverRunState.OPEN
        elif self._state.target_position == self._state.position == \
                self.CLOSED_POSITION:
            return CoverRunState.CLOSED
        elif self._state.target_position < self._state.position:
            return CoverRunState.CLOSING
        elif self._state.target_position > self._state.position:
            return CoverRunState.OPENING
        return CoverRunState.STOPPED

    async def _notify_state(self, publish_topic):
        _LOGGER.info(f'[{self}] send state={self._state}')
        coros = []

        state = {'linkquality': self.linkquality}
        for sensor_name, value in (
            (BATTERY_ENTITY, self._state.battery),
            (ILLUMINANCE_ENTITY, self._state.illuminance),
        ):
            if any(
                x['name'] == sensor_name
                for x in self.entities.get(SENSOR_DOMAIN, [])
            ):
                state[sensor_name] = self.transform_value(value)

        coros.append(publish_topic(
            topic=self._get_topic(self.STATE_TOPIC),
            value=json.dumps(state),
        ))

        covers = self.entities.get(COVER_DOMAIN, [])
        for cover in covers:
            if cover['name'] == COVER_ENTITY:
                cover_state = {
                    'state': self._state.run_state.value,
                    'position': self._state.position,
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
                    self._state.position = await self._get_position()
                    self._state.motor_speed = await self._get_motor_speed()
                    if self._state.position == self._state.target_position:
                        if self._state.position == self.CLOSED_POSITION:
                            _LOGGER.info(
                                f'[{self}] Minimum position reached. '
                                f'Set to CLOSED',
                            )
                            self._state.run_state = CoverRunState.CLOSED
                        elif self._state.position == self.OPEN_POSITION:
                            _LOGGER.info(
                                f'[{self}] Maximum position reached. '
                                f'Set to OPEN',
                            )
                            self._state.run_state = CoverRunState.OPEN
                else:
                    _LOGGER.debug(f'[{self}] check for full state')
                    await self._get_full_state()
                await self._notify_state(publish_topic)
                timer = 0
            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

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
                        f'[{self}] unknown action postfix {action_postfix}',
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

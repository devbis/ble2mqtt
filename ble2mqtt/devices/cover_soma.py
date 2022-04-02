import asyncio as aio
import logging
import typing as ty
import uuid
from dataclasses import dataclass
from enum import Enum

from ..protocols.soma import MotorCommandCodes, SomaProtocol
from .base import SENSOR_DOMAIN, BaseCover, ConnectionMode, CoverRunState
from .uuids import BATTERY, SOFTWARE_VERSION

_LOGGER = logging.getLogger(__name__)

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


class SomaCover(SomaProtocol, BaseCover):
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
            **super().entities,
            SENSOR_DOMAIN: [
                {
                    'name': BATTERY_ENTITY,
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                    'entity_category': 'diagnostic',
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

    def get_values_by_entities(self):
        return {
            BATTERY_ENTITY: self._state.battery,
            ILLUMINANCE_ENTITY: self._state.illuminance,
            self.COVER_ENTITY: {
                'state': self._state.run_state.value,
                'position': self._state.position,
            },
        }

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
        await self._update_full_state()

    def _handle_position(self, value):
        self._state.position = value

    def _handle_charging(self, *, charging_level, panel_level):
        self._state.illuminance = charging_level

    def _handle_motor_run_state(self, run_state: MotorCommandCodes):
        # Ignore run state.
        # We calculate run state on position and target position
        pass

    async def _update_full_state(self):
        await self._update_running_state()
        self._state.battery = await self._get_battery()
        self._state.target_position = await self._get_target_position()
        self._state.illuminance = \
            (await self._get_light_and_panel())['charging_level']
        self._state.run_state = self._get_run_state()

    async def _update_running_state(self):
        self._state.position = await self._get_position()
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
                    await self._update_running_state()
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
                    await self._update_full_state()
                await self._notify_state(publish_topic)
                timer = 0
            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

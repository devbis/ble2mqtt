import asyncio as aio
import logging
import typing as ty
import uuid
from dataclasses import dataclass
from enum import Enum

from ..protocols.am43 import AM43Protocol
from .base import SENSOR_DOMAIN, BaseCover, ConnectionMode, CoverRunState

_LOGGER = logging.getLogger(__name__)

BATTERY_ENTITY = 'battery'
ILLUMINANCE_ENTITY = 'illuminance'

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


class AM43Cover(AM43Protocol, BaseCover):
    NAME = 'am43'
    MANUFACTURER = 'Blind'
    DATA_CHAR = BLINDS_CONTROL
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
        self._model = 'AM43'
        self._state = AM43State()
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
        await self.client.start_notify(
            self.DATA_CHAR,
            self.notification_callback,
        )
        await self._update_full_state()

    async def _update_running_state(self):
        await self._get_position()

    async def _update_full_state(self):
        await super()._update_full_state()

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
                    await self._update_full_state()
                await self._notify_state(publish_topic)
                timer = 0
            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

    def handle_battery(self, value):
        self._state.battery = value

    def handle_position(self, value):
        self._state.position = value

    def handle_illuminance(self, value):
        self._state.illuminance = value

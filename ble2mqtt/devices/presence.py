import asyncio as aio
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from bleak.backends.device import BLEDevice

from .base import BINARY_SENSOR_DOMAIN, Device

logger = logging.getLogger(__name__)


@dataclass
class SensorState:
    presence: bool = False
    last_check: datetime = None


class Presence(Device):
    NAME = 'presence'
    SENSOR_CLASS = SensorState
    SUPPORT_PASSIVE = True
    SUPPORT_ACTIVE = False
    MANUFACTURER = 'Generic'
    THRESHOLD = 120

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = None

    @property
    def entities(self):
        return {
            BINARY_SENSOR_DOMAIN: [
                {
                    'name': 'presence',
                    'device_class': 'presence',
                },
            ],
        }

    async def _notify_state(self, publish_topic):
        logger.info(f'[{self}] send state={self._state}')
        state = {}
        for sensor_name, value in (
                ('presence', self._state.presence),
        ):
            if any(
                x['name'] == sensor_name
                for x in self.entities.get(BINARY_SENSOR_DOMAIN, [])
            ):
                state[sensor_name] = self.transform_value(value)

        if state:
            await publish_topic(
                topic='/'.join((self.unique_id, 'state')),
                value=json.dumps(state),
            )

    def handle_advert(self, scanned_device: BLEDevice, *args, **kwargs):
        self._state = self.SENSOR_CLASS(
            presence=True,
            last_check=datetime.now(),
        )
        logger.debug(
            f'Advert received for {self}, current state: {self._state}',
        )

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        last_sent_value = None
        last_sent_time = None
        while True:
            if not self._state:
                await aio.sleep(5)
                continue

            if self._state:
                if not self.config_sent:
                    await send_config(self)
                if self._state.presence and \
                        self._state.last_check + \
                        timedelta(seconds=self.THRESHOLD) < datetime.now():
                    self._state.presence = False
                # send if changed or update value every CONNECTION_TIMEOUT secs
                if last_sent_value is None or \
                        last_sent_value != self._state.presence or \
                        (datetime.now() - last_sent_time).seconds > \
                        self.CONNECTION_TIMEOUT:
                    logger.debug(f'Try publish {self._state}')
                    await self._notify_state(publish_topic)
                    last_sent_value = self._state.presence
                    last_sent_time = datetime.now()

            await aio.sleep(1)

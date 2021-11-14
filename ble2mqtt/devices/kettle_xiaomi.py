import asyncio as aio
import json
import logging
import struct
import time
import typing as ty
import uuid
from dataclasses import dataclass
from enum import Enum

from ..protocols.xiaomi import XiaomiCipherMixin
from ..utils import format_binary
from .base import BINARY_SENSOR_DOMAIN, SENSOR_DOMAIN, ConnectionMode, Device
from .uuids import SOFTWARE_VERSION

_LOGGER = logging.getLogger(__name__)


UUID_SERVICE_KETTLE = uuid.UUID('0000fe95-0000-1000-8000-00805f9b34fb')
UUID_SERVICE_KETTLE_DATA = uuid.UUID("01344736-0000-1000-8000-262837236156")
UUID_AUTH_INIT = uuid.UUID('00000010-0000-1000-8000-00805f9b34fb')
UUID_AUTH = uuid.UUID('00000001-0000-1000-8000-00805f9b34fb')
UUID_VER = uuid.UUID('00000004-0000-1000-8000-00805f9b34fb')
UUID_STATUS = uuid.UUID('0000aa02-0000-1000-8000-00805f9b34fb')

TEMPERATURE_ENTITY = 'temperature'
KETTLE_ENTITY = 'kettle'
HEAT_ENTITY = 'heat'
AUTH_MAGIC1 = bytes([0x90, 0xCA, 0x85, 0xDE])
AUTH_MAGIC2 = bytes([0x92, 0xAB, 0x54, 0xFA])

HANDLE_AUTH = 36
HANDLE_STATUS = 60


class Mode(Enum):
    IDLE = 0x00
    HEATING = 0x01
    COOLING = 0x02
    KEEP_WARM = 0x03


class LEDMode(Enum):
    BOIL = 0x01
    KEEP_WARM = 0x02
    NONE = 0xFF


class KeepWarmType(Enum):
    BOIL_AND_COOLDOWN = 0x00
    HEAT_TO_TEMP = 0x01


@dataclass
class MiKettleState:
    mode: Mode = Mode.IDLE
    led_mode: LEDMode = LEDMode.NONE
    temperature: int = 0
    target_temperature: int = 0
    keep_warm_type: KeepWarmType = KeepWarmType.BOIL_AND_COOLDOWN
    keep_warm_time: int = 0

    FORMAT = '<BBHBBBHBBB'

    @classmethod
    def from_bytes(cls, response):
        # 00 ff 00 00 5a 28 00 00 00 01 18 00
        (
            mode,  # 0
            led_mode,  # 1
            _,  # 2-3
            target_temp,  # 4
            current_temp,  # 5
            keep_warm_type,  # 6
            keep_warm_time,  # 7,8
            _, _, _,  # 9, 10, 11
        ) = struct.unpack(cls.FORMAT, response)
        return cls(
            mode=Mode(mode),
            led_mode=LEDMode(led_mode),
            temperature=current_temp,
            target_temperature=target_temp,
            keep_warm_type=KeepWarmType(keep_warm_type),
            keep_warm_time=keep_warm_time,  # minutes
        )

    def as_dict(self):
        return {
            'mode': self.mode.name.lower(),
            'running_mode': self.led_mode.name.lower(),
            'temperature': self.temperature,
            'target_temperature': self.target_temperature,
            'keep_warm_type': self.keep_warm_type.name.lower(),
            'keep_warm_minutes_passed': self.keep_warm_time,
        }


class XiaomiKettle(XiaomiCipherMixin, Device):
    NAME = 'mikettle'
    MAC_TYPE = 'random'
    ACTIVE_SLEEP_INTERVAL = 1
    SEND_INTERVAL = 30
    MANUFACTURER = 'Xiaomi'
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_KEEP_CONNECTION

    def __init__(self, mac, product_id=275, token=None,
                 *args, loop, **kwargs):
        super().__init__(mac, *args, loop=loop, **kwargs)
        self._product_id = product_id
        if token:
            assert isinstance(token, str) and len(token) == 24
            self._token = bytes.fromhex(token)
        else:
            self._token = self.generate_random_token()
        self.queue: aio.Queue = None
        self._state: ty.Optional[MiKettleState] = None

    @property
    def entities(self):
        return {
            SENSOR_DOMAIN: [
                {
                    'name': KETTLE_ENTITY,
                    'icon': 'kettle',
                    'json': True,
                    'main_value': TEMPERATURE_ENTITY,
                    'unit_of_measurement': '\u00b0C',
                },
            ],
            BINARY_SENSOR_DOMAIN: [
                {
                    'name': HEAT_ENTITY,
                    'device_class': 'heat',
                },
            ],
        }

    def notification_handler(self, sender: int, data: bytearray):
        _LOGGER.debug("Notification: {0}: {1}".format(
            sender,
            format_binary(data),
        ))
        if sender == HANDLE_STATUS:
            self._state = MiKettleState.from_bytes(data)
        else:
            # possible senders: HANDLE_AUTH == 36
            self.queue.put_nowait((sender, data))

    async def auth(self):
        await self.client.write_gatt_char(
            UUID_AUTH_INIT,
            AUTH_MAGIC1,
            True,
        )
        await self.client.start_notify(UUID_AUTH, self.notification_handler)
        await self.client.write_gatt_char(
            UUID_AUTH,
            self.cipher(
                self.mix_a(self.reverse_mac(self.mac), self._product_id),
                self._token,
            ),
            True,
        )
        auth_response = await aio.wait_for(self.queue.get(), timeout=10)
        _LOGGER.debug(f'{self} auth response: {auth_response}')
        await self.client.write_gatt_char(
            UUID_AUTH,
            XiaomiCipherMixin.cipher(self._token, AUTH_MAGIC2),
            True,
        )
        await self.client.read_gatt_char(UUID_VER)
        await self.client.stop_notify(UUID_AUTH)

    async def get_device_data(self):
        self.queue = aio.Queue()
        await self.auth()
        self._model = 'MiKettle'
        version = await self.client.read_gatt_char(SOFTWARE_VERSION)
        if version:
            self._version = version.decode()
        _LOGGER.debug(f'{self} version: {version}')
        await self.client.start_notify(UUID_STATUS, self.notification_handler)

    async def _notify_state(self, publish_topic):
        _LOGGER.info(f'[{self}] send state={self._state}')
        state = {}
        for sensor_name, value in (
            (KETTLE_ENTITY, self._state),
        ):
            if any(
                x['name'] == sensor_name
                for x in self.entities.get(SENSOR_DOMAIN, [])
            ):
                state.update(value.as_dict())

        if state:
            state['linkquality'] = self.linkquality
            await publish_topic(
                topic=self._get_topic(self.STATE_TOPIC),
                value=json.dumps(state),
            )
        for sensor_name, value in (
            (HEAT_ENTITY, self._state.mode in [Mode.HEATING, Mode.KEEP_WARM]),
        ):
            entity = self.get_entity_by_name(BINARY_SENSOR_DOMAIN, sensor_name)
            if entity:
                await publish_topic(
                    topic=self._get_topic_for_entity(entity),
                    value=self.transform_value(value),
                )

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        send_time = None
        prev_state = None
        while True:
            await self.update_device_data(send_config)
            new_state = prev_state
            if self._state:
                new_state = (
                    self._state.mode,
                    self._state.led_mode,
                    self._state.keep_warm_type,
                )
            if self._state and (
                not send_time or
                (time.time() - send_time) > self.SEND_INTERVAL or
                new_state != prev_state
            ):
                send_time = time.time()
                prev_state = new_state
                await self._notify_state(publish_topic)
            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

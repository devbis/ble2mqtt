import asyncio as aio
import json
import logging
import uuid
from dataclasses import dataclass
from enum import Enum

from ..protocols.base import BLEQueueMixin
from ..utils import format_binary
from .base import COVER_DOMAIN, SENSOR_DOMAIN, Device

logger = logging.getLogger(__name__)

COVER_ENTITY = 'cover'

BLINDS_CONTROL = uuid.UUID("0000fe51-0000-1000-8000-00805f9b34fb")


class RunState(Enum):
    OPEN = 'open'
    OPENING = 'opening'
    CLOSED = 'closed'
    CLOSING = 'closing'
    STOPPED = 'stopped'


@dataclass
class AM43State:
    battery: int = None
    position: int = 0
    light: int = None
    run_state: RunState = RunState.CLOSED
    target_position: int = None


class AM43Cover(BLEQueueMixin, Device):
    NAME = 'am43'
    MANUFACTURER = 'Blind'
    DATA_CHAR = BLINDS_CONTROL
    ACTIVE_SLEEP_INTERVAL = 1
    SEND_DATA_PERIOD = 5
    STANDBY_SEND_DATA_PERIOD_MULTIPLIER = 12 * 5  # 5 minutes
    LINKQUALITY_TOPIC = COVER_ENTITY

    # HA notation. We convert value on setting and receiving data
    CLOSED_POSITION = 0
    OPEN_POSITION = 100

    # command IDs
    CMD_MOVE = 0x0a
    CMD_GET_BATTERY = 0xa2
    CMD_GET_LIGHT = 0xaa
    CMD_GET_POSITION = 0xa7
    CMD_SET_POSITION = 0x0d
    NOTIFY_POSITION = 0xa1

    AM43_RESPONSE_ACK = 0x5a
    AM43_RESPONSE_NACK = 0xa5
    AM43_REPLY_UNKNOWN1 = 0xa8
    AM43_REPLY_UNKNOWN2 = 0xa9

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
                    'topic': 'cover',
                    'json': True,
                    'main_value': 'battery',
                },
            ],
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._model = 'AM43'
        self._state = AM43State()

    def notification_callback(self, sender_handle: int, data: bytearray):
        self.process_data(data)
        self._ble_queue.put_nowait((sender_handle, data))

    @staticmethod
    def _convert_position(value):
        return 100 - value

    async def send_command(self, id, data: list,
                           wait_reply=True, timeout=25):
        logger.debug(f'[{self}] - send command 0x{id:x} {data}')
        cmd = bytearray([0x9a, id, len(data)] + data)
        csum = 0
        for x in cmd:
            csum = csum ^ x
        cmd += bytearray([csum])

        self.clear_ble_queue()
        await self.client.write_gatt_char(BLINDS_CONTROL, cmd)
        ret = None
        if wait_reply:
            logger.debug(f'[{self}] waiting for reply')
            ble_notification = await aio.wait_for(
                self.ble_get_notification(),
                timeout=timeout,
            )
            logger.debug(f'[{self}] reply: {ble_notification[1]}')
            ret = bytes(ble_notification[1][3:-1])
        return ret

    async def _request_position(self):
        await self.send_command(self.CMD_GET_POSITION, [0x01], True)

    async def _set_position(self, value):
        await self.send_command(
            self.CMD_SET_POSITION,
            [self._convert_position(int(value))],
            True,
        )

    async def _request_state(self):
        await self._request_position()
        await self.send_command(self.CMD_GET_BATTERY, [0x01], True)
        await self.send_command(self.CMD_GET_LIGHT, [0x01], True)

    async def get_device_data(self):
        await super().get_device_data()
        await self.client.start_notify(
            self.DATA_CHAR,
            self.notification_callback,
        )
        await self._request_state()

    def process_data(self, data: bytearray):
        if data[1] == self.CMD_GET_BATTERY:
            # b'\x9a\xa2\x05\x00\x00\x00\x00Ql'
            self._state.battery = int(data[7])
        elif data[1] == self.NOTIFY_POSITION:
            self._state.position = self._convert_position(int(data[4]))
        elif data[1] == self.CMD_GET_POSITION:
            # [9a a7 07 0e 32 00 00 00 00 30 36]
            # Bytes in this packet are:
            #  3: Configuration flags, bits are:
            #    1: direction
            #    2: operation mode
            #    3: top limit set
            #    4: bottom limit set
            #    5: has light sensor
            #  4: Speed setting
            #  5: Current position
            #  6,7: Shade length.
            #  8: Roller diameter.
            #  9: Roller type.

            self._state.position = self._convert_position(int(data[5]))
        elif data[1] == self.CMD_GET_LIGHT:
            # b'\x9a\xaa\x02\x00\x002'
            self._state.light = int(data[4]) * 12.5
        elif data[1] in [self.CMD_MOVE, self.CMD_SET_POSITION]:
            if data[3] != self.AM43_RESPONSE_ACK:
                logger.error(f'[{self}] Problem with moving: NACK')
        elif data[1] in [self.AM43_REPLY_UNKNOWN1, self.AM43_REPLY_UNKNOWN2]:
            # [9a a8 00 32]
            # [9a a9 10 00 00 00 11 00 00 00 00 01 00 00 11 00 00 00 00 22]
            pass
        else:
            logger.error(
                f'{self} BLE notification unknown response '
                f'[{format_binary(data)}]',
            )

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
                    'light': self._state.light,
                }
                coros.append(publish_topic(
                    topic='/'.join((self.unique_id, cover['name'])),
                    value=json.dumps(cover_state),
                ))
        if coros:
            await aio.gather(*coros)

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        # request every SEND_DATA_PERIOD if running and
        # SEND_DATA_PERIOD * STANDBY_SEND_DATA_PERIOD_MULTIPLIER if in
        # standby mode

        timer = 0
        while True:
            await self.update_device_data(send_config)
            # if running notify every 5 seconds, 60 sec otherwise
            is_running = self._state.run_state in [
                RunState.OPENING,
                RunState.CLOSING,
            ]
            multiplier = (
                1 if is_running else self.STANDBY_SEND_DATA_PERIOD_MULTIPLIER
            )

            timer += self.ACTIVE_SLEEP_INTERVAL
            if timer >= self.SEND_DATA_PERIOD * multiplier:
                if is_running:
                    logger.debug(f'[{self}] check for position')
                    await self._request_position()
                    if self._state.position == self.CLOSED_POSITION:
                        logger.info(
                            f'[{self}] Minimum position reached. Set to CLOSED',
                        )
                        self._state.run_state = RunState.CLOSED
                    elif self._state.position == self.OPEN_POSITION:
                        logger.info(
                            f'[{self}] Maximum position reached. Set to OPEN',
                        )
                        self._state.run_state = RunState.OPEN
                else:
                    logger.debug(f'[{self}] check for full state')
                    await self._request_state()
                await self._notify_state(publish_topic)
                timer = 0
            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

    async def _do_movement(self, movement_type, target_position):
        if movement_type == 'open':
            await self.send_command(self.CMD_MOVE, [0xdd])
            self._state.run_state = RunState.OPENING
        elif movement_type == 'close':
            await self.send_command(self.CMD_MOVE, [0xee])
            self._state.run_state = RunState.CLOSING
        elif movement_type == 'position' and target_position is not None:
            if self.CLOSED_POSITION <= target_position <= self.OPEN_POSITION:
                await self._set_position(target_position)
                if self._state.position > target_position:
                    self._state.target_position = target_position
                    self._state.run_state = RunState.CLOSING
                elif self._state.position < target_position:
                    self._state.target_position = target_position
                    self._state.run_state = RunState.OPENING
                else:
                    self._state.target_position = None
                    if target_position == self.OPEN_POSITION:
                        self._state.run_state = RunState.OPEN
                    elif target_position == self.CLOSED_POSITION:
                        self._state.run_state = RunState.CLOSED
                    else:
                        self._state.run_state = RunState.STOPPED
            else:
                logger.error(
                    f'[{self}] Incorrect position value: '
                    f'{repr(target_position)}',
                )
        else:
            await self.send_command(self.CMD_MOVE, [0xcc])
            self._state.run_state = RunState.STOPPED

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
            entity_name, postfix = self.get_entity_from_topic(message['topic'])
            if entity_name == COVER_ENTITY:
                value = self.transform_value(value)
                target_position = None
                if postfix == self.SET_POSTFIX:
                    logger.info(
                        f'[{self}] set mode {entity_name} value={value}',
                    )
                    if value.lower() == 'open':
                        movement_type = 'position'
                        target_position = self.OPEN_POSITION
                    elif value.lower() == 'close':
                        movement_type = 'position'
                        target_position = self.CLOSED_POSITION
                    else:
                        movement_type = 'stop'
                elif postfix == self.SET_POSITION_POSTFIX:
                    movement_type = 'position'
                    logger.info(
                        f'[{self}] set position {entity_name} value={value}',
                    )
                    try:
                        target_position = int(value)
                    except ValueError:
                        pass
                else:
                    raise NotImplementedError()

                while True:
                    try:
                        await self._do_movement(movement_type, target_position)
                        await self._notify_state(publish_topic)
                        break
                    except ConnectionError as e:
                        logger.exception(str(e))
                    await aio.sleep(5)

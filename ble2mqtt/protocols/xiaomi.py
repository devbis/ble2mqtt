import asyncio as aio
import logging
import uuid

from ..devices.base import BaseDevice
from ..devices.uuids import DEVICE_NAME

logger = logging.getLogger(__name__)

FIRMWARE_VERSION = uuid.UUID('00002a26-0000-1000-8000-00805f9b34fb')


class XiaomiPoller(BaseDevice):
    DATA_CHAR: uuid.UUID = None
    BATTERY_CHAR: uuid.UUID = None
    RECONNECTION_TIMEOUT = 60
    MANUFACTURER = 'Xiaomi'

    def __init__(self, *args, loop, **kwargs):
        super().__init__(*args, loop=loop, **kwargs)
        self._stack = aio.LifoQueue(loop=loop)
        self._state = None

    async def get_device_data(self):
        await self.client.start_notify(
            self.DATA_CHAR,
            self.notification_handler,
        )
        version = await self._read_with_timeout(FIRMWARE_VERSION)
        if isinstance(version, (bytes, bytearray)):
            self._version = version.decode()
        name = await self._read_with_timeout(DEVICE_NAME)
        if isinstance(name, (bytes, bytearray)):
            self._model = name.decode()
        return []

    def filter_notifications(self, sender):
        return True

    def notification_handler(self, sender, data: bytearray):
        logger.debug("Notification: {0}: {1}".format(
            sender,
            ' '.join(format(x, '02x') for x in data),
        ))
        if self.filter_notifications(sender):
            self._stack.put_nowait(data)

    async def read_and_send_data(self, publish_topic):
        raise NotImplementedError()

    async def handle_active(self, publish_topic, send_config, *args, **kwargs):
        logger.debug(f'Wait {self} for connecting...')
        sec_to_wait_connection = 0
        while True:
            if not self.client.is_connected:
                if sec_to_wait_connection >= 30:
                    raise TimeoutError(
                        f'{self} not connected for 30 sec in handle()',
                    )
                sec_to_wait_connection += 1
                await aio.sleep(1)
                continue
            try:
                logger.debug(f'{self} connected!')
                # in case of bluetooth error populating queue
                # could stop and will wait for self._stack.get() forever
                await self.update_device_data(send_config)
                await aio.wait_for(
                    self.read_and_send_data(publish_topic),
                    timeout=15,
                )
            except ValueError as e:
                logger.error(f'[{self}] Cannot read values {str(e)}')
            else:
                await self.close()
                return
            await aio.sleep(1)

    async def handle_passive(self, publish_topic, send_config, *args, **kwargs):
        while True:
            if not self._state:
                await aio.sleep(5)
                continue
            logger.debug(f'Try publish {self._state}')
            if self._state and self._state.temperature and self._state.humidity:
                await self.update_device_data(send_config)
                await self._notify_state(publish_topic)
            await aio.sleep(self.CONNECTION_TIMEOUT)

    async def handle(self, *args, **kwargs):
        if self.passive:
            return await self.handle_passive(*args, **kwargs)
        return await self.handle_active(*args, **kwargs)

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
        self.connection_event = aio.Event()

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

    async def handle(self, publish_topic, send_config, *args, **kwargs):
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
                await self.read_and_send_data(publish_topic)
            except ValueError as e:
                logger.error(f'Cannot read values {str(e)}')
            else:
                await self.close()
                return
            await aio.sleep(1)

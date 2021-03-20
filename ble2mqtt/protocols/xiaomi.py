import asyncio as aio
import logging
import uuid

from ..devices.base import Sensor, SubscribeAndSetDataMixin

logger = logging.getLogger(__name__)


class XiaomiPoller(SubscribeAndSetDataMixin, Sensor):
    DATA_CHAR: uuid.UUID = None
    BATTERY_CHAR: uuid.UUID = None
    MANUFACTURER = 'Xiaomi'

    def __init__(self, *args, loop, **kwargs):
        super().__init__(*args, loop=loop, **kwargs)
        self._stack = aio.LifoQueue(loop=loop)

    def process_data(self, data):
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
                sec_to_wait_connection += self.NOT_READY_SLEEP_INTERVAL
                await aio.sleep(self.NOT_READY_SLEEP_INTERVAL)
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

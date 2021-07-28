import asyncio as aio
import logging
import typing as ty
from functools import partial

from ..helpers import done_callback
from ..utils import format_binary

logger = logging.getLogger(__name__)


class BLEQueueMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ble_queue = aio.Queue()

    def notification_callback(self, sender_handle: int, data: bytearray):
        """
        This method must be used as notification callback for BLE connection
        """
        logger.debug(
            f'{self} notification: {sender_handle}: {format_binary(data)}',
        )
        self._ble_queue.put_nowait((sender_handle, data))

    def clear_ble_queue(self):
        if hasattr(self._ble_queue, '_queue'):
            self._ble_queue._queue.clear()

    async def ble_get_notification(self) -> ty.Tuple[int, bytes]:
        return await self._ble_queue.get()


class BaseCommand:
    def __init__(self, cmd, *args, **kwargs):
        self.cmd = cmd
        self.answer = aio.Future()


class SendAndWaitReplyMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cmd_queue: aio.Queue[BaseCommand] = aio.Queue()
        self._cmd_queue_task: ty.Optional[aio.Task] = None

    async def add_cmd_to_queue(self, cmd: BaseCommand):
        await self.cmd_queue.put(cmd)

    def run_queue_handler(self):
        self.clear_cmd_queue()
        self._cmd_queue_task = aio.create_task(self._handle_cmd_queue())
        self._cmd_queue_task.add_done_callback(partial(
            done_callback,
            f'{self} handle_queue() stopped unexpectedly',
        ))

    async def stop_queue_handler(self):
        if self._cmd_queue_task is not None:
            self._cmd_queue_task.cancel()
            try:
                await self._cmd_queue_task
            except aio.CancelledError:
                pass
            self._cmd_queue_task = None

    def clear_cmd_queue(self):
        if hasattr(self.cmd_queue, '_queue'):
            self.cmd_queue._queue.clear()

    async def _handle_cmd_queue(self):
        while True:
            command = await self.cmd_queue.get()
            try:
                await self.process_command(command)
            except aio.CancelledError:
                logger.info(f'{self} cmd queue handler stopped')
                raise
            except Exception as e:
                if command and not command.answer.done():
                    command.answer.set_exception(e)
                logger.exception(
                    f'{self} raise an error in handle_queue, ignore it',
                )

    async def process_command(self, command: BaseCommand):
        raise NotImplementedError()

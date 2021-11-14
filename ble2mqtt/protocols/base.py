import abc
import asyncio as aio
import logging
import typing as ty

from ..devices.base import BaseDevice
from ..utils import format_binary

_LOGGER = logging.getLogger(__name__)


class BLEQueueMixin(BaseDevice, abc.ABC):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._ble_queue = aio.Queue(loop=self._loop)

    def notification_callback(self, sender_handle: int, data: bytearray):
        """
        This method must be used as notification callback for BLE connection
        """
        _LOGGER.debug(f'Notification: {sender_handle}: {format_binary(data)}')
        self._loop.call_soon_threadsafe(
            self._ble_queue.put_nowait, (sender_handle, data),
        )

    def clear_ble_queue(self):
        if hasattr(self._ble_queue, '_queue'):
            self._ble_queue._queue.clear()

    async def ble_get_notification(self, timeout) -> ty.Tuple[int, bytes]:
        ble_response_task = aio.create_task(self._ble_queue.get())
        disconnect_wait_task = aio.create_task(self.disconnected_event.wait())
        await aio.wait(
            [ble_response_task, disconnect_wait_task],
            timeout=timeout,
            return_when=aio.FIRST_COMPLETED,
        )
        if ble_response_task.done():
            disconnect_wait_task.cancel()
            try:
                await disconnect_wait_task
            except aio.CancelledError:
                pass
            return await ble_response_task
        else:
            ble_response_task.cancel()
            try:
                await ble_response_task
            except aio.CancelledError:
                pass
            raise ConnectionError(
                f'{self} cannot fetch response, device is offline',
            )


class BaseCommand:
    def __init__(self, cmd, *args, wait_reply, timeout, **kwargs):
        self.cmd = cmd
        self.answer = aio.Future()
        self.wait_reply = wait_reply
        self.timeout = timeout


class SendAndWaitReplyMixin(BaseDevice, abc.ABC):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cmd_queue: aio.Queue[BaseCommand] = aio.Queue(loop=self._loop)
        self._cmd_queue_task = aio.ensure_future(
            self._handle_cmd_queue(),
            loop=self._loop,
        )
        self._cmd_queue_task.add_done_callback(
            self._queue_handler_done_callback,
        )

    def clear_cmd_queue(self):
        if hasattr(self.cmd_queue, '_queue'):
            self.cmd_queue._queue.clear()

    async def _handle_cmd_queue(self):
        while True:
            command = await self.cmd_queue.get()
            try:
                await self.process_command(command)
            except aio.CancelledError:
                _LOGGER.exception(f'{self} _handle_cmd_queue is cancelled!')
                raise
            except Exception as e:
                if command and not command.answer.done():
                    command.answer.set_exception(e)
                _LOGGER.exception(
                    f'{self} raise an error in handle_queue, ignore it',
                )

    async def process_command(self, command):
        raise NotImplementedError()

    def _queue_handler_done_callback(self, future: aio.Future):
        exc_info = None
        try:
            exc_info = future.exception()
        except aio.CancelledError:
            pass

        if exc_info is not None:
            exc_info = (  # type: ignore
                type(exc_info),
                exc_info,
                exc_info.__traceback__,
            )
            _LOGGER.exception(
                f'{self} _handle_cmd_queue() stopped unexpectedly',
                exc_info=exc_info,
            )

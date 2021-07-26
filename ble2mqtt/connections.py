import asyncio as aio
import logging
import typing as ty
from contextlib import asynccontextmanager
from enum import Enum
from functools import partial

import aio_mqtt

from .bt import (BLUETOOTH_RESTARTING, ListOfBtConnectionErrors,
                 handle_ble_exceptions, restart_bluetooth)
from .devices.base import ConnectionTimeoutError, Device
from .helpers import (done_callback, handle_returned_tasks,
                      run_tasks_and_cancel_on_first_return)

logger = logging.getLogger(__name__)

FAILURE_LIMIT = 5


class MqttHelper:
    def __init__(self) -> None:
        pass


class ConnectionRules(Enum):
    PASSIVE = 0
    ACTIVE = 1
    ON_DEMAND = 2


class SafeRunContext:
    def __init__(self):
        self.exception = None


class BaseConnectionManager:
    def __init__(self, device: Device, mqtt_client: aio_mqtt.Client, *,
                 on_connect=None) -> None:
        super().__init__()
        self.connection_manager_task = None
        self.device: Device = device
        self.mqtt_client: aio_mqtt.Client = mqtt_client
        self.on_connect: ty.Optional[callable] = on_connect

        self.missing_device_count = 0
        self.failure_count = 0

    async def handle_bt_connection(self):
        while True:
            async with self.safe_run() as sr:
                try:
                    await self.device.connect()
                    self.missing_device_count = 0
                    self.failure_count = 0
                except ConnectionTimeoutError:
                    self.missing_device_count += 1
                    logger.error(
                        f'[{self.device}] connection problem, '
                        f'attempts={self.missing_device_count}',
                    )
                    await aio.sleep(self.device.RECONNECTION_SLEEP_INTERVAL)
                    continue

                await self.device.connected_event.wait()
                if self.on_connect:
                    await self.on_connect()
                await self.device.disconnected_event.wait()
            if sr.exception:
                logger.info(
                    f'{self.device} sleep for '
                    f'{self.device.RECONNECTION_SLEEP_INTERVAL} seconds '
                    f'because of error',
                )
                await aio.wait(
                    [
                        self.device.need_reconnection.wait(),
                        aio.sleep(self.device.RECONNECTION_SLEEP_INTERVAL),
                    ],
                    return_when=aio.FIRST_COMPLETED,
                )
            else:
                reason = ''
                if self.device.on_demand_connection:
                    reason = ' due to on-demand policy'
                    logger.info(
                        f'{self.device} sleep for '
                        f'{self.device.ON_DEMAND_POLL_TIME} seconds{reason}',
                    )
                    wait_time = self.device.ON_DEMAND_POLL_TIME
                else:
                    logger.info(
                        f'{self.device} sleep for '
                        f'{self.device.ON_DEMAND_POLL_TIME} seconds{reason}',
                    )
                    wait_time = self.device.RECONNECTION_SLEEP_INTERVAL
                await aio.wait(
                    [
                        self.device.need_reconnection.wait(),
                        aio.sleep(wait_time),
                    ],
                    return_when=aio.FIRST_COMPLETED,
                )

    @asynccontextmanager
    async def safe_run(self, postprocess=None) -> SafeRunContext:
        async with BLUETOOTH_RESTARTING:
            logger.debug(f'[{self.device}] safe_run: Check for lock')
        context = SafeRunContext()
        try:
            async with handle_ble_exceptions():
                yield context
        except aio.CancelledError:
            raise
        except KeyboardInterrupt:
            raise
        except (ConnectionError, TimeoutError, aio.TimeoutError) as e:
            context.exception = e
            self.missing_device_count += 1
            logger.exception(
                f'[{self.device}] connection problem, '
                f'attempts={self.missing_device_count}',
            )
        except ListOfBtConnectionErrors as e:
            context.exception = e
            if 'Device with address' in str(e) and 'was not found' in str(e):
                self.missing_device_count += 1
                logger.exception(
                    f'Error while connecting to {self.device}, {e} {repr(e)}, '
                    f'attempts={self.missing_device_count}',
                )
            else:
                self.failure_count += 1
                logger.exception(
                    f'Error while connecting to {self.device}, {e} {repr(e)}, '
                    f'failure_count={self.failure_count}',
                )

            # sometimes LYWSD03MMC devices remain connected
            # and doesn't advert their presence.
            # If cannot find device for several attempts, restart
            # the bluetooth chip
            if (
                self.missing_device_count >=
                    self.device.CONNECTION_FAILURES_LIMIT
            ):
                logger.error(
                    f'Device {self.device} was not found for '
                    f'{self.missing_device_count} times. Restarting bluetooth.',
                )
                self.missing_device_count = 0
                await restart_bluetooth()
        finally:
            await self.device.disconnect()
            try:
                if postprocess:
                    await postprocess()
            except aio.CancelledError:
                raise
            except Exception:
                pass

        if self.failure_count >= FAILURE_LIMIT:
            await restart_bluetooth()
            self.failure_count = 0

    async def _loop(self, get_coros: callable):
        tasks = [aio.create_task(t) for t in get_coros()]
        logger.debug(f'[{self.device}] tasks are created')

        def clean():
            canceled = []
            for t in tasks:
                if not t.done():
                    t.cancel()
                    canceled.append(t)
            for t in canceled:
                try:
                    t.result()
                except aio.CancelledError:
                    pass

        async with self.safe_run(postprocess=clean):
            await run_tasks_and_cancel_on_first_return(*tasks)
            if self.device.disconnected_event.is_set():
                logger.debug(f'{self.device} has disconnected')
            finished = [t for t in tasks if not t.cancelled()]
            await handle_returned_tasks(*finished)

    async def run(self, get_coros: callable):
        async with self.connection_manager():
            while True:
                await self._loop(get_coros)
                await aio.sleep(1)

    @asynccontextmanager
    async def connection_manager(self):
        raise NotImplementedError()


# class PassiveConnectionManager(BaseConnectionManager):
#     @asynccontextmanager
#     async def connection_manager(self):
#         yield


class ActiveConnectionManager(BaseConnectionManager):
    # 1. run connection task
    # 2. create working tasks
    # 3. run all tasks and wait for first is finished
    # 4. close all tasks
    # 5. disconnect
    # 5. repeat

    @asynccontextmanager
    async def connection_manager(self):
        self.connection_manager_task = aio.create_task(
            self.handle_bt_connection(),
        )
        self.connection_manager_task.add_done_callback(partial(
            done_callback,
            f'{self.device} connection_manager_task stopped unexpectedly',
        ))
        try:
            yield
        finally:
            try:
                self.connection_manager_task.cancel()
            except aio.CancelledError:
                pass

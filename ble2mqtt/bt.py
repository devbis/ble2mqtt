import asyncio as aio
import contextlib
import logging
import os
import traceback
from contextlib import asynccontextmanager

from bleak import BleakError

logger = logging.getLogger(__name__)
ListOfBtConnectionErrors = (
    BleakError,
    aio.TimeoutError,

    # dbus-next exceptions:
    # AttributeError: 'NoneType' object has no attribute 'call'
    AttributeError,
    # https://github.com/hbldh/bleak/issues/409
    EOFError,
)

BLUETOOTH_RESTARTING = aio.Lock()


def hardware_exception_occurred(exception):
    ex_str = str(exception)
    return (
        'org.freedesktop.DBus.Error.ServiceUnknown' in ex_str or
        'org.freedesktop.DBus.Error.NoReply' in ex_str or
        'org.freedesktop.DBus.Error.AccessDenied' in ex_str or
        'org.bluez.Error.Failed: Connection aborted' in ex_str or
        'org.bluez.Error.NotReady' in ex_str or
        'org.bluez.Error.InProgress' in ex_str
    )


async def restart_bluetooth(interface='hci0'):

    @contextlib.contextmanager
    def ntries(tries=5):
        for _ in range(tries):
            try:
                yield
                break
            except TimeoutError:
                pass

    if BLUETOOTH_RESTARTING.locked():
        await aio.sleep(9)
        return
    async with BLUETOOTH_RESTARTING:
        print('=====================')
        traceback.print_stack()
        print('=====================')
        logger.warning('Restarting bluetoothd...')
        with ntries():
            logger.warning(f'--> hciconfig down')
            proc = await aio.create_subprocess_exec(
                'hciconfig', interface, 'down',
            )
            logger.warning(f'--> wait for proc')
            await aio.wait_for(proc.wait(), 10)
        with ntries():
            logger.warning(f'--> bluetoothd restart')
            if os.path.exists('/etc/openwrt_release'):
                proc = await aio.create_subprocess_exec(
                    '/etc/init.d/bluetoothd', 'restart',
                )
            else:
                proc = await aio.create_subprocess_exec(
                    'hciconfig', interface, 'reset',
                )
            logger.warning(f'--> wait for proc')
            await aio.wait_for(proc.wait(), 10)
        await aio.sleep(3)
        with ntries():
            logger.warning(f'--> hciconfig up')
            proc = await aio.create_subprocess_exec(
                'hciconfig', interface, 'up',
            )
            logger.warning(f'--> wait for proc')
            await aio.wait_for(proc.wait(), 10)
        await aio.sleep(5)
        logger.warning(f'Restarting {interface} interface finished')


@asynccontextmanager
async def handle_ble_exceptions():
    try:
        yield
    except ListOfBtConnectionErrors as e:
        if hardware_exception_occurred(e):
            await restart_bluetooth()
            await aio.sleep(3)
        raise

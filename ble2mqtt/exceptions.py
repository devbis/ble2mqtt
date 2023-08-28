import asyncio as aio
import logging
import os
from contextlib import asynccontextmanager

import aio_mqtt
from bleak import BleakError

ListOfConnectionErrors = (
    BleakError,
    aio.TimeoutError,

    # dbus-next exceptions:
    # AttributeError: 'NoneType' object has no attribute 'call'
    AttributeError,
    # https://github.com/hbldh/bleak/issues/409
    EOFError,
)

ListOfMQTTConnectionErrors = (
        aio_mqtt.ConnectionLostError,
        aio_mqtt.ConnectionClosedError,
        aio_mqtt.ServerDiedError,
        BrokenPipeError,
)


_LOGGER = logging.getLogger(__name__)

# initialize in a loop
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


async def restart_bluetooth(adapter: str):
    if BLUETOOTH_RESTARTING.locked():
        await aio.sleep(9)
        return
    async with BLUETOOTH_RESTARTING:
        _LOGGER.warning('Restarting bluetoothd...')
        proc = await aio.create_subprocess_exec(
            'hciconfig', adapter, 'down',
        )
        await proc.wait()

        if os.path.exists('/etc/init.d/bluetoothd'):
            proc = await aio.create_subprocess_exec(
                '/etc/init.d/bluetoothd', 'restart',
            )
            await proc.wait()

        elif os.path.exists('/etc/init.d/bluetooth'):
            proc = await aio.create_subprocess_exec(
                '/etc/init.d/bluetooth', 'restart',
            )
            await proc.wait()

        else:
            _LOGGER.error('init.d bluetoothd script not found')

        await aio.sleep(3)
        proc = await aio.create_subprocess_exec(
            'hciconfig', adapter, 'up',
        )
        await proc.wait()
        await aio.sleep(5)
        _LOGGER.warning('Restarting bluetoothd finished')


@asynccontextmanager
async def handle_ble_exceptions(adapter: str):
    try:
        yield
    except ListOfConnectionErrors as e:
        if hardware_exception_occurred(e):
            await restart_bluetooth(adapter)
            await aio.sleep(3)
        raise

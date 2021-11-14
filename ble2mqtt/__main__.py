import asyncio as aio
import json
import logging
import os

from ble2mqtt.__version__ import VERSION
from ble2mqtt.ble2mqtt import Ble2Mqtt

from .devices import registered_device_types

_LOGGER = logging.getLogger(__name__)

is_shutting_down: aio.Lock = aio.Lock()


async def shutdown(loop, service: Ble2Mqtt, signal=None):
    """Cleanup tasks tied to the service's shutdown."""
    if is_shutting_down.locked():
        return
    async with is_shutting_down:
        if signal:
            _LOGGER.info(f"Received exit signal {signal.name}...")
        _LOGGER.info("Closing ble2mqtt service")
        await service.close()
        tasks = [t for t in aio.all_tasks() if t is not aio.current_task()]

        [task.cancel() for task in tasks]

        _LOGGER.info(f"Cancelling {len(tasks)} outstanding tasks")
        try:
            await aio.wait_for(
                aio.gather(*tasks, return_exceptions=True),
                timeout=10,
            )
        except (Exception, aio.CancelledError):
            _LOGGER.exception(f'Cancelling caused error: {tasks}')
        loop.stop()


def handle_exception(loop, context, service):
    _LOGGER.error(f"Caught exception: {context}")
    loop.default_exception_handler(context)
    exception_str = context.get('task') or context.get('future') or ''
    exception = context.get('exception')
    if 'BleakClientBlueZDBus._disconnect_monitor()' in \
            str(repr(exception_str)):
        # There is some problem when Bleak waits for disconnect event
        # and asyncio destroys the task and raises
        # Task was destroyed but it is pending!
        # Need further investigating.
        # Skip this exception for now.
        _LOGGER.info("Ignore this exception.")
        return

    if "'NoneType' object has no attribute" in \
            str(repr(exception)):
        # lambda _: self._disconnecting_event.set()
        # AttributeError: 'NoneType' object has no attribute 'set'
        # await self._disconnect_monitor_event.wait()
        # AttributeError: 'NoneType' object has no attribute 'wait'
        _LOGGER.info("Ignore this exception.")
        return

    if isinstance(exception, BrokenPipeError):
        # task = asyncio.ensure_future(self._cleanup_all())
        # in bluezdbus/client.py: _parse_msg() can fail while remove_match()
        _LOGGER.info("Ignore this exception.")
        return

    _LOGGER.info("Shutting down...")
    aio.create_task(shutdown(loop, service))


async def amain(config):
    loop = aio.get_running_loop()

    service = Ble2Mqtt(
        reconnection_interval=10,
        loop=loop,
        host=config['mqtt_host'],
        port=config['mqtt_port'],
        user=config.get('mqtt_user'),
        password=config.get('mqtt_password'),
        base_topic=config['base_topic'],
        mqtt_config_prefix=config['mqtt_config_prefix'],
    )

    loop.set_exception_handler(
        lambda *args: handle_exception(*args, service=service),
    )

    devices = config.get('devices') or []
    for device in devices:
        try:
            mac = device.pop('address')
            typ = device.pop('type')
        except (ValueError, IndexError):
            continue
        klass = registered_device_types[typ]
        service.register(
            klass,
            mac=mac,
            loop=loop,
            **device,
        )

    try:
        await service.start()
    except KeyboardInterrupt:
        _LOGGER.info('Exiting...')
    finally:
        await service.close()


def main():
    os.environ.setdefault('BLE2MQTT_CONFIG', '/etc/ble2mqtt.json')
    config = {}
    if os.path.exists(os.environ['BLE2MQTT_CONFIG']):
        try:
            with open(os.environ['BLE2MQTT_CONFIG'], 'r') as f:
                config = json.load(f)
        except FileNotFoundError:
            pass

    config = {
        'mqtt_host': 'localhost',
        'mqtt_port': 1883,
        'base_topic': 'ble2mqtt',
        'mqtt_config_prefix': 'b2m_',
        'log_level': 'INFO',
        # 'hci_device': 'hci0',
        **config,
    }

    logging.basicConfig(level=config['log_level'].upper())
    # logging.getLogger('bleak.backends.bluezdbus.scanner').setLevel('INFO')
    _LOGGER.info(f'Starting BLE2MQTT version {VERSION}')

    try:
        aio.run(amain(config), debug=(config['log_level'].upper() == 'DEBUG'))
    except KeyboardInterrupt:
        pass
    _LOGGER.info('Bye.')


if __name__ == '__main__':
    main()

import asyncio as aio
import json
import logging
import os

from ble2mqtt.__version__ import VERSION
from ble2mqtt.ble2mqtt import Ble2Mqtt
from ble2mqtt.compat import get_bleak_version

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


def get_ssl_context(config):
    if config.get('mqtt_tls') != "True":
        return None
    import ssl
    ca_cert = config.get('mqtt_ca')
    client_cert = config.get('mqtt_cert')
    client_keyfile = config.get('mqtt_key')
    client_keyfile_password = config.get('mqtt_key_password')
    ca_verify = config.get('mqtt_ca_verify') != "False"
    context = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
    if ca_cert is not None:
        context.load_verify_locations(ca_cert)
    else:
        context.load_default_certs()
    context.check_hostname = ca_verify
    context.verify_mode = ssl.CERT_REQUIRED if ca_verify else ssl.CERT_NONE
    if client_keyfile is not None:
        context.load_cert_chain(
            client_cert,
            client_keyfile,
            client_keyfile_password,
        )
    return context


async def amain(config):
    loop = aio.get_running_loop()

    service = Ble2Mqtt(
        reconnection_interval=10,
        loop=loop,
        host=config['mqtt_host'],
        port=config['mqtt_port'],
        user=config.get('mqtt_user'),
        password=config.get('mqtt_password'),
        ssl=get_ssl_context(config),
        base_topic=config['base_topic'],
        mqtt_config_prefix=config['mqtt_config_prefix'],
        hci_adapter=config['hci_adapter'],
        legacy_color_mode=config['legacy_color_mode'],
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
        'hci_adapter': 'hci0',
        'legacy_color_mode': False,
        **config,
    }

    logging.basicConfig(
        format='%(asctime)s %(levelname)s: %(message)s',
        level=config['log_level'].upper(),
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    # logging.getLogger('bleak.backends.bluezdbus.scanner').setLevel('INFO')
    _LOGGER.info(
        'Starting BLE2MQTT version %s, bleak %s, adapter %s',
        VERSION,
        get_bleak_version(),
        config["hci_adapter"]
    )

    try:
        aio.run(amain(config), debug=(config['log_level'].upper() == 'DEBUG'))
    except KeyboardInterrupt:
        pass
    _LOGGER.info('Bye.')


if __name__ == '__main__':
    main()

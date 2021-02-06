import asyncio as aio
import json
import logging
import os
import signal

from ble2mqtt.ble2mqtt import Ble2Mqtt

from .devices import registered_device_types


async def shutdown(loop, service: Ble2Mqtt, signal=None):
    """Cleanup tasks tied to the service's shutdown."""
    if signal:
        logging.info(f"Received exit signal {signal.name}...")
    logging.info("Closing ble2mqtt service")
    await service.close()
    tasks = [t for t in aio.all_tasks() if t is not aio.current_task()]

    [task.cancel() for task in tasks]

    logging.info(f"Cancelling {len(tasks)} outstanding tasks")
    await aio.gather(*tasks, return_exceptions=True)
    loop.stop()


def handle_exception(loop, context, service):
    # context["message"] will always be there; but context["exception"] may not
    msg = context.get("exception", context["message"])
    logging.error(f"Caught exception: {msg}")
    logging.info("Shutting down...")
    aio.create_task(shutdown(loop, service))


def main():
    logging.basicConfig(level='INFO')
    # logging.getLogger('bleak.backends.bluezdbus.scanner').setLevel('INFO')
    loop = aio.get_event_loop()

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
        **config,
    }

    service = Ble2Mqtt(
        reconnection_interval=10,
        loop=loop,
        host=config['mqtt_host'],
        port=config['mqtt_port'],
        user=config.get('mqtt_user'),
        password=config.get('mqtt_password'),
    )

    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
    for sig in signals:
        loop.add_signal_handler(
            sig,
            lambda s=sig: aio.create_task(shutdown(loop, service, s)),
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
        service.register(klass(
            mac=mac,
            loop=loop,
            **device,
        ))

    try:
        loop.create_task(service.start())
        loop.run_forever()
    finally:
        loop.run_until_complete(service.close())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == '__main__':
    main()

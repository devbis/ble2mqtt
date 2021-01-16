import asyncio as aio
import json
import logging
import os

from ble2mqtt.ble2mqtt import Ble2Mqtt

from .devices import registered_device_types


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
        except Exception:
            raise

    config = {
        'mqtt_host': 'localhost',
        'mqtt_port': 1883,
        **config,
    }

    server = Ble2Mqtt(
        reconnection_interval=10,
        loop=loop,
        host=config['mqtt_host'],
        port=config['mqtt_port'],
        user=config.get('mqtt_user'),
        password=config.get('mqtt_password'),
    )

    devices = config.get('devices') or []
    for device in devices:
        try:
            mac = device.pop('address')
            typ = device.pop('type')
        except (ValueError, IndexError):
            continue
        klass = registered_device_types[typ]
        server.register(klass(
            mac=mac,
            loop=loop,
            **device,
        ))

    try:
        loop.run_until_complete(server.start())
    except KeyboardInterrupt:
        pass

    finally:
        loop.run_until_complete(server.close())
        loop.run_until_complete(loop.shutdown_asyncgens())
        loop.close()


if __name__ == '__main__':
    main()

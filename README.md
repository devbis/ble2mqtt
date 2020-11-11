# Service to export BLE devices to MQTT with Home Assistant discovery

## !!! It is a very early alpha release !!! Use this software at your own risk.

Default config should be located in `/etc/ble2mqtt.json` or 
can be overridden with `BLE2MQTT_CONFIG` environment variable.

Example run command:

```sh 
BLE2MQTT_CONFIG=./ble2mqtt.json python3 ble2mqtt.py
```

The configuration file is a JSON with the following content:

```json
{
    "mqtt_host": "localhost",
    "mqtt_port": 1883,
    "mqtt_user": "",
    "mqtt_password": "",
    "devices": [
        {
            "address": "11:22:33:aa:bb:cc",
            "type": "redmond200"
        },
        {
            "address": "11:22:33:aa:bb:dd",
            "type": "xiaomihtv1"
        },
        {
            "address": "11:22:33:aa:bb:ee",
            "type": "xiaomilywsd"
        }
    ]
}
```

Supported devices:

**Kettles:**
- Redmond G2xx series (redmond200)

**Humidity sensors:**
- Xiaomi MJ_HT_V1 (xiaomihtv1)
- Xiaomi LYWSD03MMC (xiaomilywsd)

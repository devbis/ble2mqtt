# Service to export BLE devices to MQTT with Home Assistant discovery

## !!! It is a very early alpha release !!! 

**Use this software at your own risk.**

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


## OpenWRT installation

Execute the following commands in the terminal:

```shell script
opkg update
opkg install python3-twisted python3-pip python3-asyncio
pip install -U ble2mqtt
```

Create the configuration file in /etc/ble2mqtt.json and
append your devices.

Bluetooth must be turned on.

```shell script
hciconfig hci0 up
```

Run the service in background

```shell script
python -m ble2mqtt 2> /tmp/ble2mqtt.log &
```

## Container

Build the image as:

```shell script
podman build .
```

Then use it as a mounted volume as:

```shell script
podman run -d --net=host -v $PWD/ble2mqtt.json.sample:/etc/ble2mqtt.json:z 5966e7eaef47
```

NOTE: `--net=host` is required as it needs to use the bluetooth interface

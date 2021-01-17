# Service to export BLE devices to MQTT with Home Assistant discovery

## !!! It is a very early alpha release !!! 

**Use this software at your own risk.**

Default config should be located in `/etc/ble2mqtt.json` or 
can be overridden with `BLE2MQTT_CONFIG` environment variable.

Example run command:

```sh 
BLE2MQTT_CONFIG=./ble2mqtt.json ble2mqtt
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
            "address": "11:22:33:aa:cc:aa",
            "type": "presence"
        },
        {
            "address": "11:22:33:aa:bb:cc",
            "type": "redmond200"
        },
        {
            "address": "11:22:33:aa:bb:dd",
            "type": "xiaomihtv1"
        },
        {
            "address": "11:22:34:aa:bb:dd",
            "type": "xiaomihtv1",
            "passive": false
        },
        {
            "address": "11:22:33:aa:bb:ee",
            "type": "xiaomilywsd"
        },
        {
            "address": "11:22:33:aa:bb:ff",
            "type": "xiaomilywsd_atc"
        }
    ]
}
```

Supported devices:

**Any device**
- Any bluetooth device can work as a presence tracker

**Kettles:**
- Redmond G2xx series (redmond200)

**Humidity sensors:**
- Xiaomi MJ_HT_V1 (xiaomihtv1)
- Xiaomi LYWSD03MMC (xiaomilywsd)
- Xiaomi LYWSD03MMC with custom ATC firmware (xiaomilywsd_atc)

By default, a device works in the passive mode without connection by 
listening to advertisement packets from a device.
To use connection to the device provide `"passive": false` parameter.

**Supported devices in passive mode:**
- Xiaomi MJ_HT_V1 (xiaomihtv1)
- Xiaomi LYWSD03MMC with custom ATC firmware (xiaomilywsd_atc)


## OpenWRT installation

Execute the following commands in the terminal:

```shell script
opkg update
opkg install python3-pip python3-asyncio
pip3 install git+https://github.com/hbldh/bleak.git@f50a334e1173b27a8cf0a53d8ac56d9acc24fedf#egg=bleak
pip3 install -U ble2mqtt
```

Create the configuration file in /etc/ble2mqtt.json and
append your devices.

Bluetooth must be turned on.

```shell script
hciconfig hci0 up
```

Run the service in background

```shell script
ble2mqtt 2> /tmp/ble2mqtt.log &
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

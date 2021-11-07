# Service to export BLE devices to MQTT with Home Assistant discovery

## Supported devices:

### Any device
- Any bluetooth device can work as a presence tracker

### Kettles
- **Redmond RK-G2xxS series (redmond_rk_g200)**

  The default key that is used is `"ffffffffffffffff"`
  and can be omitted in the config.
  In some cases kettles don't accept it. Just use another 
  key in the config file for the device: 
  `"key": "16 random hex numbers"`

- **Mi Kettle (mikettle)**

  Use correct `product_id` for your kettle:
  - yunmi.kettle.v1: `131`
  - yunmi.kettle.v2: `275` (default)
  - yunmi.kettle.v7: `1116`

### Multi-Cookers
- **Redmond RMC-M225S, RMC-M227S (redmond_rmc_m200)**

  Notes about the key parameter you can read above for the 
  Redmond kettles. 
  *Other RMC multi-cookers may need 
  adjustments for the list of available programs, it depends
  on the device panel.*

### Humidity sensors
- **Xiaomi MJ_HT_V1 (xiaomihtv1)**
- **Xiaomi LYWSD03MMC (xiaomilywsd)** (due to the connection to the device on 
  every data fetch, it consumes more battery power. Flashing to the custom
  firmware is recommended)
- **Xiaomi LYWSD03MMC with custom ATC firmware (xiaomilywsd_atc)**
  - supported both atc1441 and pvvx formats

### Blinds
- **Generic AM43 (am43)**

  Manufacturer can be A-OK, Zemismart, etc.

### Bulbs
- **Avea RGBW bulbs (avea_rgbw)**

### Dosimeters
- **Atom Fast (atomfast)**

By default, a device works in the passive mode without connection by 
listening to advertisement packets from a device.
To use connection to the device provide `"passive": false` parameter.

**Supported devices in passive mode:**
- Xiaomi MJ_HT_V1 (xiaomihtv1)
- Xiaomi LYWSD03MMC with custom ATC firmware (xiaomilywsd_atc)


### Known issues:
- *High cpu usage due to underlying library to work with bluetooth*

**Use this software at your own risk.**

## Configuration

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
    "log_level": "INFO",
    "devices": [
        {
            "address": "11:22:33:aa:cc:aa",
            "type": "presence"
        },
        {
            "address": "11:22:33:aa:bb:cc",
            "type": "redmond_rk_g200",
            "key": "ffffffffffffffff"
        },
        {
            "address": "11:22:33:aa:bb:c0",
            "type": "redmond_rmc_m200",
            "key": "ffffffffffffffff"
        },
        {
            "address": "11:22:33:aa:bb:cd",
            "type": "mikettle",
            "product_id": 275
        },
        {
            "address": "11:22:33:aa:bb:de",
            "type": "am43"
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
        },
        {
            "address": "11:22:33:aa:aa:aa",
            "type": "atomfast"
        }
    ]
}
```

You can omit a line, then default value will be used.

Extra configuration parameters:
- `"base_topic"`- the default value is 'ble2mqtt'
- `"mqtt_prefix"`- a prefix to distinguish ble devices from other instances and
  programs. The default value is 'b2m_'.

Devices accept `friendly_name` parameter to replace mac address in device
names for Home Assistant.


## Installation on OpenWRT

Execute the following commands in the terminal:

```shell script
opkg update
opkg install python3-pip python3-asyncio
pip3 install "bleak>=0.11.0"
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

Add a service script to start:

```
cat <<EOF > /etc/init.d/ble2mqtt
#!/bin/sh /etc/rc.common

START=98
USE_PROCD=1

start_service()
{
    procd_open_instance

    procd_set_param env BLE2MQTT_CONFIG=/etc/ble2mqtt.json
    procd_set_param command /usr/bin/ble2mqtt
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
EOF
chmod +x /etc/init.d/ble2mqtt
/etc/init.d/ble2mqtt enable
/etc/init.d/ble2mqtt start
```

## Running on Xiaomi Zigbee Gateway

Due to small CPU power and increasing number of messages from bluetoothd
it is recommended to do several workarounds:

1. Use passive mode for those sensors for which this is possible. E.g. use
   custom ATC firmware for lywsd03mmc sensors
1. Restart `bluetoothd` daily and restart ble2mqtt several times a day to 
   reduce increasing CPU usage. 
   Put the following lines to the `/etc/crontabs/root`

 ```
10 0,7,17 * * * /etc/init.d/ble2mqtt restart
1 4,14 * * * /etc/init.d/bluetoothd restart
```

## Running in Container

Build the image as:

```shell script
podman build -t ble2mqtt:dev .
```

Start the container and share the config file and DBus for Bluetooth connectivity:
```shell script
podman run \
-d \
--net=host \
-v $PWD/ble2mqtt.json.sample:/etc/ble2mqtt.json:z \
-v /var/run/dbus:/var/run/dbus:z \
ble2mqtt:dev
```

Instead of sharing `/var/run/dbus`, you can export `DBUS_SYSTEM_BUS_ADDRESS`.

NOTE: `--net=host` is required as it needs to use the bluetooth interface

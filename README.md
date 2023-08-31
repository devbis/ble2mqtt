# BLE2MQTT

### Base on `devbis/ble2mqtt` approach with some changes for presence detection.</p>

### Control your Bluetooth devices with smart home

![ble2mqtt devices](./ble2mqtt.png)

## Supported devices:

### Any device
- Any bluetooth device can work as a presence tracker 
  - `"threshold"` - set the limit in 
  second when the device is considered away. The default value is 180 seconds. 
  - `"send_data_period"` - variable defines a time when status update should be pushed. Default is 60 seconds of the refresh.
  - `"sdp_activation"` - enable/disable `SEND_DATA_PERIOD` usage. Status is refreshed only when tracker has `ON` state. When is `OFF` it stops sending. Default is `True`.

### Kettles
- **Redmond RK-G2xxS series (type: redmond_rk_g200)**

  The default key that is used is `"ffffffffffffffff"`
  and can be omitted in the config.
  In some cases kettles don't accept it. Just use another 
  key in the config file for the device: 
  `"key": "16 random hex numbers"`

- **Mi Kettle (type: mikettle)**

  Use correct `product_id` for your kettle:
  - yunmi.kettle.v1: `131`
  - yunmi.kettle.v2: `275` (default)
  - yunmi.kettle.v7: `1116`

### Multi-Cookers
- **Redmond RMC-M225S, RMC-M227S (type: redmond_rmc_m200)**

  Notes about the key parameter you can read above for the 
  Redmond kettles. 
  *Other RMC multi-cookers may need 
  adjustments for the list of available programs, it depends
  on the device panel.*

### Humidity sensors
- **Xiaomi MJ_HT_V1 (type: xiaomihtv1)**
- **Xiaomi LYWSD02MMC (type: xiaomihtv1)**
- **Xiaomi LYWSD03MMC (type: xiaomilywsd)** (due to the connection to the device on 
  every data fetch, it consumes more battery power. Flashing to the custom
  firmware is recommended)
- **Xiaomi LYWSD03MMC with custom ATC firmware (xiaomilywsd_atc)**
  - supported both atc1441 and pvvx formats
- **Qingping CGDK2 (type: qingpingCGDK2)**

### Air sensors
- **Vson WP6003 (type: wp6003)**

### Shades and Blinds
- **Generic AM43 (type: am43)**

  Manufacturer can be A-OK, Zemismart, etc.
- **Soma Shades (type: soma_shades)**

### Bulbs
- **Avea RGBW bulbs (type: avea_rgbw)**

### Dosimeters
- **Atom Fast (type: atomfast)**

### Heaters
- **Ensto EPHBEBT10PR, EPHBEBT15PR (type: ensto_thermostat)**

  These devices require [manual pairing](#manual-pairing-in-linux).
  After the device is paired on the host device, see the logs for the `key` and 
  put it to the config.

  The adapter uses holiday mode to control temperature as thermostat. You cannot 
  use this feature in the official app while ble2mqtt is working.

### Battery voltage meters

- **BM2 car battery voltage meter (type: voltage_bm2)**


### Plant sensors:

- **LifeControl MCLH-09 (type: mclh09)**

  optionally, polling interval can be configured with `interval` parameter in seconds
- **Xiaomi Mi Flora (type: miflora)**

  optionally, polling interval can be configured with `interval` parameter in seconds

By default, a device works in the passive mode without connection by 
listening to advertisement packets from a device.
To use connection to the device provide `"passive": false` parameter.

**Supported devices in passive mode:**
- Xiaomi MJ_HT_V1 (xiaomihtv1)
- Xiaomi LYWSD03MMC with custom ATC firmware (xiaomilywsd_atc)
- Any device as presence tracker

## Manual pairing in Linux

Some devices (e.g. Ensto heaters) require paired connection to work with it. 
You need to pair the device with linux machine before using it. 

Find out MAC addresses of your devices. Put the device in pairing mode if it is supported.

Open console and run `bluetoothctl` command. It is a command line tool to work with BLE devices.
Wait for the prompt 

```
[bluetooth]#
```

Print a command to enable scanning. Linux must know the device is present before pairing.

```
[bluetooth]# scan on
```

Wait for MAC address of the device appears in the list of found devices.
Print a pairing command (replace MAC address to the one from your device)

```
[bluetooth]# pair 90:fd:00:00:00:01
```

On successful pairing you'll see a message:

```
[CHG] Device 90:FD:00:00:00:01 Paired: yes
Pairing successful
```
You can proceed with the next configuration steps now.


## Selinux issues

If using SELinux and you are experience issues, see [README.md](selinux/README.md).

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
            "address": "11:22:33:aa:bb:c1",
            "type": "ensto_thermostat",
            "# see logs after pairing and put the key to config": "",
            "key": "00112233"
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
        },
        {
            "address": "11:22:33:aa:aa:bb",
            "type": "voltage_bm2"
        },
        {
            "address": "11:22:33:aa:aa:bc",
            "type": "mclh09",
            "interval": 600
        },
        {
            "address": "11:22:33:aa:aa:bd",
            "type": "miflora",
            "interval": 500
        }
    ]
}
```

You can omit a line, then default value will be used.

Extra configuration parameters:
- `"base_topic"`- the default value is 'ble2mqtt'
- `"mqtt_prefix"`- a prefix to distinguish ble devices from other instances and
  programs. The default value is 'b2m_'.
- `"hci_adapter"` - an adapter to use. The default value is "hci0"

Devices accept `friendly_name` parameter to replace mac address in device
names for Home Assistant.


## Systemd unit file to start on boot

Put the following content to the unit file `/etc/systemd/system/ble2mqtt.service`

```
[Unit]
Description=ble2mqtt bridge

[Service]
Type=Simple
ExecStart=/usr/local/bin/ble2mqtt
User=ble2mqtt
Group=ble2mqtt
Wants=bluetooth.target

[Install]
WantedBy=multi-user.target
```

The user and group should match the owner and group of the configuration file /etc/ble2mqtt.json.

Afterwards you simply have to enable and start the service:

```sh
sudo systemctl daemon-reload
sudo systemctl enable ble2mqtt
sudo systemctl start ble2mqtt
```

## Installation on OpenWRT

Execute the following commands in the terminal:

```sh
opkg update
opkg install python3-pip python3-asyncio
pip3 install "bleak>=0.11.0"
pip3 install -U git+https://github.com/orzechszek/ble2mqtt.git
```

Create the configuration file in /etc/ble2mqtt.json and
append your devices.

Bluetooth must be turned on.

```sh
hciconfig hci0 up
```

Run the service in background

```sh
ble2mqtt 2> /tmp/ble2mqtt.log &
```

Add a service script to start:

```sh
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

```sh
docker build -t ble2mqtt .
```

Start the container and share the config file:
```sh
docker run -d --net=host --cap-add=NET_ADMIN --privileged --name ble2mqtt -e TZ=Europe/Warsaw -v /opt/ble2mqtt/data:/data -v /var/run/dbus:/var/run/dbus ble2mqtt:latest
```

NOTE: `--net=host` and `--cap-add=NET_ADMIN` is required as it needs to use and control the bluetooth interface

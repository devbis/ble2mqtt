#!/bin/sh

if [ -f "/var/run/dbus" ]; then
    ble2mqtt
else 
    service dbus start
    bluetoothd &
    ble2mqtt
fi


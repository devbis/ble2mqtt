FROM alpine:latest
RUN apk update
RUN apk add --no-cache git python3 py3-pip bluez
RUN apk add --no-cache --virtual .gyp make g++
RUN pip3 install asyncio
RUN pip3 install "bleak>=0.11.0"
RUN pip3 install --no-cache-dir -U git+https://github.com/orzechszek/ble2mqtt.git
ENV BLE2MQTT_CONFIG /data/ble2mqtt.json
CMD ["ble2mqtt"]

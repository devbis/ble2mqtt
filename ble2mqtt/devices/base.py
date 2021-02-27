import abc
import asyncio as aio
import logging
import typing as ty

from bleak import BleakClient, BleakError

from ble2mqtt.utils import rssi_to_linkquality

logger = logging.getLogger(__name__)
registered_device_types = {}


BINARY_SENSOR_DOMAIN = 'binary_sensor'
SENSOR_DOMAIN = 'sensor'
LIGHT_DOMAIN = 'light'
SWITCH_DOMAIN = 'switch'


class RegisteredType(type):
    def __new__(mcs, clsname, superclasses, attributedict):
        newclass = type.__new__(mcs, clsname, superclasses, attributedict)
        # condition to prevent base class registration
        if superclasses:
            if newclass.NAME is not None:
                registered_device_types[newclass.NAME] = newclass
        return newclass


class BaseDevice(metaclass=RegisteredType):
    NAME = None
    SUPPORT_PASSIVE = False
    SUPPORT_ACTIVE = True

    # Whether we should stop handle task on disconnect or not
    # if true wait more to publish data to topics
    DEVICE_DROPS_CONNECTION = False

    def __init__(self, *args, loop, **kwargs):
        self._loop = loop
        self.client: BleakClient = None
        if kwargs.get('passive') and not self.SUPPORT_PASSIVE:
            raise NotImplementedError(
                'This device doesn\'t support passive mode',
            )
        self.passive = kwargs.get('passive', self.SUPPORT_PASSIVE)
        self.config_sent = False

    async def close(self):
        pass

    async def _read_with_timeout(self, char, timeout=5):
        try:
            result = await aio.wait_for(
                self.client.read_gatt_char(char),
                timeout=timeout,
                loop=self._loop,
            )
        except (aio.TimeoutError, BleakError, AttributeError):
            logger.exception(f'Cannot connect to device {self}')
            result = None
        return result

    @staticmethod
    def transform_value(value):
        if isinstance(value, bool):
            return 'ON' if value else 'OFF'
        if not isinstance(value, str):
            return value
        vl = value.lower()
        if vl in ['0', 'off', 'no']:
            return 'OFF'
        elif vl in ['1', 'on', 'yes']:
            return 'ON'
        return value

    @property
    def unique_id(self):
        raise None

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        raise NotImplementedError()

    def handle_advert(self, *args, **kwargs):
        raise NotImplementedError()


class Device(BaseDevice):
    MQTT_VALUES = None
    SET_POSTFIX = 'set'
    RECONNECTION_TIMEOUT = 3
    MAC_TYPE = 'public'
    MANUFACTURER = None
    CONNECTION_TIMEOUT = 60
    CONNECTION_FAILURES_LIMIT = 100

    def __init__(self, mac, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.disconnected_event = aio.Event()
        self.message_queue = aio.Queue()
        self.mac = mac
        self._model = None
        self._version = None
        self._manufacturer = self.MANUFACTURER
        self._rssi = None

        assert set(self.entities.keys()) <= {
            BINARY_SENSOR_DOMAIN,
            SENSOR_DOMAIN,
            LIGHT_DOMAIN,
            SWITCH_DOMAIN,
        }

    def get_entity_from_topic(self, topic: str):
        if topic.startswith(self.unique_id):
            topic = topic[len(self.unique_id):]
        if topic.endswith(self.SET_POSTFIX):
            topic = topic[:-len(self.SET_POSTFIX)]
        return topic.strip('/')

    @property
    def subscribed_topics(self):
        return [
            f'{self.unique_id}/{entity["name"]}/{self.SET_POSTFIX}'
            for cls, items in self.entities.items()
            for entity in items
            if cls in [SWITCH_DOMAIN, LIGHT_DOMAIN]
        ]

    @property
    def manufacturer(self):
        return self._manufacturer

    @property
    def model(self):
        return self._model

    @property
    def version(self):
        return self._version

    @property
    def dev_id(self):
        return self.mac.replace(':', '').lower()

    @property
    def unique_id(self):
        parts = [self.manufacturer, self.model, self.dev_id]
        return '_'.join([p.replace(' ', '_') for p in parts if p])

    @property
    def rssi(self):
        return self._rssi

    @rssi.setter
    def rssi(self, value):
        self._rssi = value

    @property
    def linkquality(self):
        if self.rssi is None:
            return None
        return rssi_to_linkquality(self.rssi)

    @property
    @abc.abstractmethod
    def entities(self):
        return {}

    async def handle_messages(self, *args, **kwargs):
        while True:
            await aio.sleep(1)

    async def update_device_data(self, send_config):
        """
        Call this method on each iteration in handle.
        It will update rssi and config
        """
        if not self.config_sent:
            await send_config(self)
        if self.client:  # in passive mode, client is None
            self.rssi = self.client._properties.get('RSSI')

    def __str__(self):
        return self.unique_id

    def __repr__(self):
        return f'<Device:{str(self)}>'

    async def add_incoming_message(self, topic: str, value):
        await self.message_queue.put({
            'topic': topic,
            'value': value,
        })

    async def get_device_data(self):
        """Here put the initial configuration for the device"""
        pass

    async def get_client(self, **kwargs) -> BleakClient:
        assert self.MAC_TYPE in ('public', 'random')
        return BleakClient(self.mac, address_type=self.MAC_TYPE, **kwargs)

    async def connect(self):
        if self.passive:
            return

        self.client = await self.get_client(
            disconnected_callback=self._on_disconnect,
        )
        self.disconnected_event.clear()
        try:
            await self.client.connect()
        except (Exception, aio.CancelledError):
            self.disconnected_event.set()
            raise
        logger.info(f'Connected to {self.client.address}')

    def _on_disconnect(self, client, *args):
        logger.debug(f'Client {client.address} disconnected, device={self}')
        self.disconnected_event.set()

    async def close(self):
        try:
            connected = self.client and self.client.is_connected
        # exception on macos when checking for is_connected()
        except AttributeError:
            connected = True
        if connected:
            await self.client.disconnect()

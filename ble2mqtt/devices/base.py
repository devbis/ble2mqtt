import asyncio as aio
import logging
import typing as ty

from bleak import BleakClient, BleakError

logger = logging.getLogger(__name__)
registered_device_types = {}


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

    def __init__(self, *args, loop, **kwargs):
        self._loop = loop
        self.client: BleakClient = None
        self.bt_lock = aio.Lock()

    async def close(self):
        pass

    async def _read_with_timeout(self, char, timeout=5):
        try:
            result = await aio.wait_for(
                self.client.read_gatt_char(char),
                timeout=timeout,
                loop=self._loop,
            )
        except Exception:
            logger.exception('Cannot connect to device')
            result = None
        return result

    @staticmethod
    def transform_value(value):
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


class Device(BaseDevice):
    MQTT_VALUES = None
    ON_OFF = False
    SET_POSTFIX = 'set'
    RECONNECTION_TIMEOUT = 3
    REQUIRE_CONNECTION = False
    MAC_TYPE = 'public'
    MANUFACTURER = None

    def __init__(self, mac, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.disconnected_future: ty.Optional[aio.Future] = None
        self.message_queue = aio.Queue()
        self._mac = mac
        self._model = None
        self._version = None
        self._manufacturer = self.MANUFACTURER
        self.connection_event = aio.Event()

    def get_entity_from_topic(self, topic: str):
        return topic.removesuffix(self.SET_POSTFIX).removeprefix(
            self.unique_id,
        ).strip('/')

    @property
    def subscribed_topics(self):
        return [
            f'{self.unique_id}/{entity["name"]}/{self.SET_POSTFIX}'
            for cls, items in self.entities.items()
            for entity in items
            if cls in ['switch', 'light']
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
        return self._mac.replace(':', '').lower()

    @property
    def unique_id(self):
        parts = [self.manufacturer, self.model, self.dev_id]
        return '_'.join([p for p in parts if p])

    @property
    def entities(self):
        return {}

    async def handle(self, *args, **kwargs):
        raise NotImplementedError()

    async def handle_messages(self, *args, **kwargs):
        while True:
            await aio.sleep(1)

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

    async def get_client(self) -> BleakClient:
        assert self.MAC_TYPE in ('public', 'random')
        return BleakClient(self._mac, address_type=self.MAC_TYPE)

    async def connect(self):
        self.client = await self.get_client()
        self.disconnected_future = self._loop.create_future()
        try:
            async with self.bt_lock:
                await self.client.connect()
                self.connection_event.set()
            self.client.set_disconnected_callback(self.on_disconnect)
        except BleakError:
            self.connection_event.clear()
            self.client.set_disconnected_callback(None)
            raise
        logger.info(f'Connected to {self.client.address}')
        return self.disconnected_future

    def on_disconnect(self, client, *args):
        logger.info(f'Client {client.address} disconnected, device={self}')
        self.connection_event.clear()
        if self.disconnected_future.done() or \
                self.disconnected_future.cancelled():
            raise NotImplementedError(
                f'disconnected_future for device={self} is '
                f'{self.disconnected_future}',
            )
        self.disconnected_future.set_result(client.address)
        self.client.set_disconnected_callback(None)
        self.client = None

    async def close(self):
        try:
            if self.client and await self.client.is_connected():
                await self.client.disconnect()
        # exception on macos when checking for is_connected()
        except AttributeError:
            pass

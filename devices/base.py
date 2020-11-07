import asyncio as aio
import logging
import typing as ty

from bleak import BleakClient, BleakError

logger = logging.getLogger(__name__)
registered_device_types = {}


class RegisteredType(type):
    def __new__(cls, clsname, superclasses, attributedict):
        newclass = type.__new__(cls, clsname, superclasses, attributedict)
        # condition to prevent base class registration
        if superclasses:
            if newclass.NAME is not None:
                registered_device_types[newclass.NAME] = newclass
        return newclass


class BaseDevice(metaclass=RegisteredType):
    NAME = None

    def __init__(self, loop, *args, **kwargs):
        self._loop = loop
        self.client: BleakClient = None
        self.bt_lock = aio.Lock()


class Device(BaseDevice):
    MQTT_VALUES = None
    ON_OFF = False
    SET_POSTFIX = 'set'
    RECONNECTION_TIMEOUT = 3
    REQUIRE_CONNECTION = False

    def __init__(self, mac, *args, loop, **kwargs) -> None:
        super().__init__(loop)
        self.disconnected_future: ty.Optional[aio.Future] = None
        self.message_queue = aio.Queue()
        self._mac = mac
        self._model = None
        self._version = None
        self._manufacturer = None
        self.connection_event = aio.Event()

    def get_entity_from_topic(self, topic: str):
        return topic.removesuffix(self.SET_POSTFIX).removeprefix(
            self.unique_id,
        ).strip('/')

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
    def subscribed_topics(self):
        return [
            f'{self.unique_id}/{entity["name"]}/{self.SET_POSTFIX}'
            for cls, items in self.entities.items()
            for entity in items
            if cls in ['switch']
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
        return self._mac

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
        raise NotImplementedError()

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

    def on_disconnect(self, client, *args):
        logger.info(f'Client {client.address} disconnected')
        self.connection_event.clear()
        if not self.disconnected_future.done() and \
                not self.disconnected_future.cancelled():
            self.disconnected_future.set_result(client.address)
            self.client.set_disconnected_callback(None)
            self.client = None

import asyncio as aio
import logging

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
        self.bt_lock = aio.Lock()


class Device(BaseDevice):
    MQTT_VALUES = None
    ON_OFF = False
    SET_POSTFIX = 'set'
    RECONNECTION_TIMEOUT = 3
    REQUIRE_CONNECTION = False

    def __init__(self, loop, *args, **kwargs) -> None:
        super().__init__(loop)
        self.client: BleakClient = None
        self.disconnected_future = None
        self._model = None
        self._version = None

    async def connect(self):
        self.disconnected_future = self._loop.create_future()
        try:
            async with self.bt_lock:
                await self.client.connect()
            self.client.set_disconnected_callback(self.on_disconnect)
            logger.info(f'Connected to {self.client.address}')
        except BleakError:
            self.client.set_disconnected_callback(None)
            raise

    def on_disconnect(self, client, *args):
        logger.info(f'Client {client.address} disconnected')
        self.disconnected_future.set_result(client.address)

    def get_entity_from_topic(self, topic: str):
        return topic.removesuffix(self.SET_POSTFIX).removeprefix(
            self.unique_id,
        ).strip('/')

    @staticmethod
    def transform_value(value):
        vl = value.lower() if isinstance(value, str) else value
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
        return None

    @property
    def model(self):
        return self._model

    @property
    def version(self):
        return self._version

    @property
    def dev_id(self):
        return None

    @property
    def unique_id(self):
        parts = [self.manufacturer, self.model, self.dev_id]
        return '_'.join([p for p in parts if p])

    async def init(self):
        pass

    async def process_topic(self, topic: str, value, *args, **kwargs):
        raise NotImplementedError()

    @property
    def entities(self):
        return {}

    async def handle(self, *args, **kwargs):
        raise NotImplementedError()

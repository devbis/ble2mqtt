import abc
import asyncio as aio
import json
import logging
import uuid
import warnings
from enum import Enum

from bleak import BleakClient, BleakError

from ..devices.uuids import DEVICE_NAME, FIRMWARE_VERSION
from ..utils import format_binary, rssi_to_linkquality

logger = logging.getLogger(__name__)
registered_device_types = {}


BINARY_SENSOR_DOMAIN = 'binary_sensor'
SENSOR_DOMAIN = 'sensor'
LIGHT_DOMAIN = 'light'
SWITCH_DOMAIN = 'switch'
COVER_DOMAIN = 'cover'

DEFAULT_STATE_TOPIC = ''  # send to the parent topic


class CoverRunState(Enum):
    OPEN = 'open'
    OPENING = 'opening'
    CLOSED = 'closed'
    CLOSING = 'closing'
    STOPPED = 'stopped'


class ConnectionTimeoutError(ConnectionError):
    pass


class RegisteredType(abc.ABCMeta):
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
    # Used for devices that disconnects a few seconds after connect
    DEVICE_DROPS_CONNECTION = False

    def __init__(self, *args, loop, **kwargs):
        self._loop = loop
        self.client: BleakClient = None
        if kwargs.get('passive') and not self.SUPPORT_PASSIVE:
            raise NotImplementedError(
                'This device doesn\'t support passive mode',
            )
        self._is_passive = kwargs.get('passive', self.SUPPORT_PASSIVE)
        self.config_sent = False

    @property
    def is_passive(self):
        return self._is_passive

    async def disconnect(self):
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
        raise NotImplementedError()

    @property
    def unique_name(self):
        raise NotImplementedError()

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        raise NotImplementedError()

    def handle_advert(self, *args, **kwargs):
        raise NotImplementedError()


class Device(BaseDevice, abc.ABC):
    MQTT_VALUES = None
    SET_POSTFIX = 'set'
    SET_POSITION_POSTFIX = 'set_position'  # for covers. Consider rework
    MAC_TYPE = 'public'
    MANUFACTURER = None
    CONNECTION_FAILURES_LIMIT = 100
    RECONNECTION_SLEEP_INTERVAL = 60
    ACTIVE_SLEEP_INTERVAL = 60
    PASSIVE_SLEEP_INTERVAL = 60
    LINKQUALITY_TOPIC = None
    STATE_TOPIC = DEFAULT_STATE_TOPIC
    ON_DEMAND_POLL_TIME = RECONNECTION_SLEEP_INTERVAL

    # secs to sleep if not connected or no data in passive mode
    NOT_READY_SLEEP_INTERVAL = 5

    def __init__(self, mac, *args, prefix, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        # event triggered after device is connected
        self.connected_event = aio.Event()
        # event triggered after device is disconnected
        self.disconnected_event = aio.Event()
        # event triggered after device is successfully connected and
        # set up all initialization routines, like reading initial state
        # and subscribing to char notifications
        self.initialized_event = aio.Event()
        self.message_queue = aio.Queue()
        self.mac = mac
        self.prefix = prefix
        self._model = None
        self._version = None
        self._manufacturer = self.MANUFACTURER
        self._rssi = None
        self.on_demand_connection = False

        assert set(self.entities.keys()) <= {
            BINARY_SENSOR_DOMAIN,
            SENSOR_DOMAIN,
            LIGHT_DOMAIN,
            SWITCH_DOMAIN,
            COVER_DOMAIN,
        }

    def _get_topic(self, topic):
        return '/'.join(filter(None, (self.unique_id, topic)))

    def _get_topic_for_entity(self, entity, *, skip_unique_id=False):
        subtopic = entity.get('topic', self.STATE_TOPIC)
        if skip_unique_id:
            return subtopic
        return self._get_topic(subtopic)

    def get_entity_by_name(self, domain: str, name: str):
        return next(
            (e for e in self.entities.get(domain, []) if e['name'] == name),
            None,
        )

    def get_entity_subtopic_from_topic(self, topic: str) -> tuple:
        action_postfix = None
        if topic.startswith(self.unique_id):
            topic = topic[len(self.unique_id):]
        for postfix in [self.SET_POSTFIX, self.SET_POSITION_POSTFIX]:
            if topic.endswith(postfix):
                action_postfix = postfix
                topic = topic[:-len(postfix)]
                break
        return topic.strip('/'), action_postfix

    @property
    def subscribed_topics(self):
        return [
            f'{self._get_topic_for_entity(entity)}/{self.SET_POSTFIX}'
            for cls, items in self.entities.items()
            for entity in items
            if cls in [SWITCH_DOMAIN, LIGHT_DOMAIN, COVER_DOMAIN]
        ] + [
            f'{self._get_topic_for_entity(entity)}/{self.SET_POSITION_POSTFIX}'
            for cls, items in self.entities.items()
            for entity in items
            if cls in [COVER_DOMAIN]
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
        # name and manufacturer can change while working, e.g. when
        # a device sends his name. To avoid changing topics use
        # the ID based on mac address only
        return f'{self.prefix}{self.dev_id}'

    @property
    def unique_name(self):
        # can change over time. Don't use it as an identifier
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

    async def wait_for_mqtt_message(self):
        if self.on_demand_connection:
            message = await self.message_queue.get()
            logger.info(f'[{self}] New message {message}')
            # await self.cancel_disconnect_timer()
            if not self.client.is_connected:
                logger.info(f'[{self}] set need_reconnection event')
                self.need_reconnection.set()
            await self.initialized_event.wait()
            return message
        else:
            try:
                message = await aio.wait_for(
                    self.message_queue.get(),
                    timeout=60,
                )
                if not self.client.is_connected:
                    raise ConnectionError()
                return message
            except aio.TimeoutError:
                await aio.sleep(1)
        return None

    async def update_device_data(self, send_config):
        """
        Call this method on each iteration in handle.
        It will update rssi and config
        """
        if not self.config_sent:
            await send_config()
        if self.client:  # in passive mode, client is None
            props = self.client._properties
            self.rssi = props.get('RSSI')

    def __str__(self):
        return self.unique_name

    def __repr__(self):
        return f'<Device:{str(self)}>'

    async def add_incoming_message(self, topic: str, value):
        await self.message_queue.put({
            'topic': topic,
            'value': value,
        })

    async def get_device_data(self):
        """Here put the initial configuration for the device"""
        warnings.warn("Deprecated")
        return await self.on_first_connection()

    async def on_first_connection(self):
        """
        Here put the initial configuration for the device on first connection
        """
        pass

    async def on_each_connection(self):
        """Here put code that is updated on every connection"""
        pass

    async def get_client(self, **kwargs) -> BleakClient:
        assert self.MAC_TYPE in ('public', 'random')
        return BleakClient(self.mac, address_type=self.MAC_TYPE, **kwargs)

    async def connect(self):
        if self.is_passive:
            return

        self.client = await self.get_client(
            disconnected_callback=self._on_disconnect,
        )
        self.disconnected_event.clear()
        try:
            await aio.wait_for(self.client.connect(), timeout=15.0)
        except aio.TimeoutError as e:
            logger.warning(f'[{self}] timed out on connect.')
            self.disconnected_event.set()
            raise ConnectionTimeoutError() from e
        except (Exception, aio.CancelledError):
            self.disconnected_event.set()
            raise
        self.connected_event.set()
        logger.info(f'Connected to {self.client.address}')

    def _on_disconnect(self, client, *args):
        logger.debug(f'Client {client.address} disconnected, device={self}')
        self.disconnected_event.set()
        self.connected_event.clear()
        self.initialized_event.clear()

    async def disconnect(self):
        try:
            connected = self.client and self.client.is_connected
        # exception on macos when checking for is_connected()
        except AttributeError:
            connected = True
        if connected:
            try:
                await aio.wait_for(
                    self.client.disconnect(),
                    timeout=10,
                )
            except aio.TimeoutError:
                logger.exception(f'{self} not disconnected in 10 secs')
        if not self.disconnected_event.is_set():
            self.disconnected_event.set()
        await super().disconnect()


class Sensor(Device):
    # a list of state properties that must be not None at least one of them
    # to send data.
    # E.g. only battery updated, but wait for temperature and humidity
    REQUIRED_VALUES = ()

    def __init__(self, mac, *args, loop, **kwargs) -> None:
        super().__init__(mac, *args, loop=loop, **kwargs)
        self._state = None

    @property
    def entities(self):
        raise NotImplementedError()

    async def get_device_data(self):
        name = await self._read_with_timeout(DEVICE_NAME)
        if isinstance(name, (bytes, bytearray)):
            self._model = name.decode().strip('\0')
        version = await self._read_with_timeout(FIRMWARE_VERSION)
        if isinstance(version, (bytes, bytearray)):
            self._version = version.decode().strip('\0')

    def get_entity_map(self):
        state = {}
        for domain, entities in self.entities.items():
            for entity in entities:
                sensor_name = entity['name']
                value = getattr(self._state, sensor_name, None)
                if value is not None:
                    state[sensor_name] = self.transform_value(value)
        if self.REQUIRED_VALUES and not any(
            state.get(x) for x in self.REQUIRED_VALUES
        ):
            return {}
        return state

    async def _notify_state(self, publish_topic):
        logger.info(f'[{self}] send state={self._state}')
        state = self.get_entity_map()
        if state:
            state['linkquality'] = self.linkquality
            await publish_topic(
                topic=self._get_topic(self.STATE_TOPIC),
                value=json.dumps(state),
            )

    async def do_active_loop(self, publish_topic):
        await self._notify_state(publish_topic)

    async def do_passive_loop(self, publish_topic):
        await self._notify_state(publish_topic)

    async def handle_active(self, publish_topic, send_config, *args, **kwargs):
        while True:
            await self.update_device_data(send_config)
            if not self._state:
                await aio.sleep(self.NOT_READY_SLEEP_INTERVAL)
                continue

            await self.do_active_loop(publish_topic)
            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

    async def handle_passive(self, publish_topic, send_config, *args, **kwargs):
        while True:
            if not self._state:
                await aio.sleep(self.NOT_READY_SLEEP_INTERVAL)
                continue

            await self.update_device_data(send_config)
            await self.do_passive_loop(publish_topic)
            await aio.sleep(self.PASSIVE_SLEEP_INTERVAL)

    async def handle(self, *args, **kwargs):
        if self.is_passive:
            return await self.handle_passive(*args, **kwargs)
        return await self.handle_active(*args, **kwargs)


class SubscribeAndSetDataMixin:
    DATA_CHAR: uuid.UUID = None

    def filter_notifications(self, sender):
        return True

    def process_data(self, data: bytearray):
        self._state = self.SENSOR_CLASS.from_data(data)

    def notification_handler(self, sender, data: bytearray):
        logger.debug("Mixin: {0} notification: {1}: {2}".format(
            self,
            sender,
            format_binary(data),
        ))
        if self.filter_notifications(sender):
            self.process_data(data)

    async def get_device_data(self):
        if self.DATA_CHAR:
            await self.client.start_notify(
                self.DATA_CHAR,
                self.notification_handler,
            )
        await super().get_device_data()


class SupportOnDemandConnection(BaseDevice, abc.ABC):
    """
    Allow keep connection off until a message from MQTT received or
    periodic poll run
    """

    ON_DEMAND_CONNECTION = False
    ON_DEMAND_POLL_TIME = 60 * 60  # connect and request state every 60 minutes
    ON_DEMAND_KEEP_ALIVE_TIME = 60 * 2  # keep connected for 2 minutes

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.on_demand_connection = self.ON_DEMAND_CONNECTION
        if 'on_demand_connection' in kwargs:
            self.on_demand_connection = kwargs['on_demand_connection']
        self.need_reconnection = aio.Event()
        self.disconnect_delay_task = None

    async def init_disconnect_timer(self):
        if self.disconnect_delay_task:
            # postpone disconnection
            await self.cancel_disconnect_timer()
        if self.on_demand_connection:
            async def _sleep_and_disconnect():
                await aio.sleep(self.ON_DEMAND_KEEP_ALIVE_TIME)
                logger.info(
                    f'[{self}] disconnect after inactivity due to '
                    f'on-demand policy',
                )
                await self.disconnect()

            logger.info(
                f'{self} set callback for disconnection, sleep for '
                f'{self.ON_DEMAND_KEEP_ALIVE_TIME} and then disconnect',
            )
            self.disconnect_delay_task = aio.create_task(
                _sleep_and_disconnect(),
            )

    async def cancel_disconnect_timer(self):
        if self.disconnect_delay_task:
            logger.info(f'{self} cancel disconnected callback')
            self.disconnect_delay_task.cancel()
            try:
                await self.disconnect_delay_task
            except aio.CancelledError:
                pass
            self.disconnect_delay_task = None

    async def connect(self):
        if self.disconnect_delay_task:
            await self.cancel_disconnect_timer()
        await super().connect()
        self.need_reconnection.clear()
        if not self.is_passive:
            await self.init_disconnect_timer()

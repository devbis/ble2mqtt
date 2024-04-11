import abc
import asyncio as aio
import json
import logging
import typing as ty
import uuid
from collections import defaultdict, namedtuple
from dataclasses import asdict, dataclass
from enum import Enum

from bleak import BleakClient, BleakError
from bleak.backends.device import BLEDevice

from ..compat import get_loop_param
from ..devices.uuids import DEVICE_NAME, FIRMWARE_VERSION
from ..utils import format_binary, rssi_to_linkquality

try:
    from bleak.backends.bluezdbus.manager import get_global_bluez_manager
except ImportError:
    # bleak < 0.15
    async def nothing(): return None

    get_global_bluez_manager = nothing

_LOGGER = logging.getLogger(__name__)
registered_device_types = {}


BINARY_SENSOR_DOMAIN = 'binary_sensor'
BUTTON_DOMAIN = 'button'
CLIMATE_DOMAIN = 'climate'
COVER_DOMAIN = 'cover'
DEVICE_TRACKER_DOMAIN = 'device_tracker'
LIGHT_DOMAIN = 'light'
SELECT_DOMAIN = 'select'
SENSOR_DOMAIN = 'sensor'
SWITCH_DOMAIN = 'switch'

DEFAULT_STATE_TOPIC = ''  # send to the parent topic


class CoverRunState(Enum):
    OPEN = 'open'
    OPENING = 'opening'
    CLOSED = 'closed'
    CLOSING = 'closing'
    STOPPED = 'stopped'


class ConnectionMode(Enum):
    PASSIVE = 0
    ACTIVE_POLL_WITH_DISCONNECT = 1
    ACTIVE_KEEP_CONNECTION = 2
    ON_DEMAND_CONNECTION = 3  # not implemented yet


class ConnectionTimeoutError(ConnectionError):
    pass


def done_callback(future: aio.Future):
    exc_info = None
    try:
        exc_info = future.exception()
    except aio.CancelledError:
        pass

    if exc_info is not None:
        exc_info = (  # type: ignore
            type(exc_info),
            exc_info,
            exc_info.__traceback__,
        )
        _LOGGER.exception(
            f'{future} stopped unexpectedly',
            exc_info=exc_info,
        )


async def extract_rssi(client: BleakClient) -> ty. Optional[int]:
    if hasattr(client, 'get_rssi'):
        return await client.get_rssi()
    try:
        if client.manager:
            # bleak >= 0.15
            props = client.manager._properties.get(
                client._device_path, {}).get("org.bluez.Device1", {})
        else:
            props = client._properties
    except AttributeError:
        return None
    return props.get('RSSI')


class RegisteredType(abc.ABCMeta):
    def __new__(mcs, clsname, superclasses, attributedict):
        newclass = type.__new__(mcs, clsname, superclasses, attributedict)
        # condition to prevent base class registration
        if superclasses and abc.ABC not in superclasses:
            if newclass.NAME is not None:
                registered_device_types[newclass.NAME] = newclass
            assert (
                not newclass.SUPPORT_ACTIVE or
                newclass.ACTIVE_CONNECTION_MODE is not None
            ), f'{clsname} requires ACTIVE_CONNECTION_MODE to be set'
        return newclass


class BaseDevice(abc.ABC, metaclass=RegisteredType):
    NAME: str = None  # type: ignore
    SUPPORT_PASSIVE: bool = False
    SUPPORT_ACTIVE: bool = True
    ACTIVE_CONNECTION_MODE: ConnectionMode = None  # type: ignore

    # Whether we should stop handle task on disconnect or not
    # if true wait more to publish data to topics
    DEVICE_DROPS_CONNECTION: bool = False

    def __init__(self, *args, loop, **kwargs):
        self._loop = loop
        self.client: BleakClient = None
        self.disconnected_event = aio.Event()
        self.disconnected_event.set()
        if kwargs.get('passive') and not self.SUPPORT_PASSIVE:
            raise NotImplementedError(
                'This device doesn\'t support passive mode',
            )
        self._is_passive = kwargs.get('passive', self.SUPPORT_PASSIVE)
        if self._is_passive:
            self._connection_mode: ConnectionMode = ConnectionMode.PASSIVE
        else:
            self._connection_mode = self.ACTIVE_CONNECTION_MODE
        self.config_sent = False

    @property
    def is_passive(self):
        return self._is_passive

    async def close(self):
        pass

    async def _read_with_timeout(self, char, timeout=5):
        try:
            result = await aio.wait_for(
                self.client.read_gatt_char(char),
                timeout=timeout,
                **get_loop_param(self._loop),
            )
        except (aio.TimeoutError, BleakError, AttributeError):
            _LOGGER.exception(f'Cannot connect to device {self}')
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

    def handle_advert(self, scanned_device: BLEDevice, adv_data):
        raise NotImplementedError()


class Device(BaseDevice, abc.ABC):
    SET_POSTFIX: str = 'set'
    SET_POSITION_POSTFIX: str = 'set_position'  # for covers. Consider rework
    SET_MODE_POSTFIX: str = 'set_mode'  # for climate
    SET_TARGET_TEMPERATURE_POSTFIX: str = 'set_temperature'  # for climate
    MAC_TYPE: str = 'public'
    MANUFACTURER: str = None  # type: ignore
    CONNECTION_FAILURES_LIMIT = 100
    RECONNECTION_SLEEP_INTERVAL = 60
    ACTIVE_SLEEP_INTERVAL = 60
    DEFAULT_PASSIVE_SLEEP_INTERVAL = 60
    # deprecated
    LINKQUALITY_TOPIC: ty.Optional[str] = None
    STATE_TOPIC: str = DEFAULT_STATE_TOPIC

    # secs to sleep if not connected or no data in passive mode
    NOT_READY_SLEEP_INTERVAL = 5

    def __init__(self, mac, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.message_queue: aio.Queue = aio.Queue(**get_loop_param(self._loop))
        self.mac = mac.lower()
        self.passive_sleep_interval = int(
            kwargs.pop('interval', self.DEFAULT_PASSIVE_SLEEP_INTERVAL),
        )
        self._suggested_area = kwargs.pop('suggested_area', None)
        self.friendly_name = kwargs.pop('friendly_name', None)
        self._model = None
        self._version = None
        self._manufacturer = self.MANUFACTURER
        self._rssi = None
        self._advertisement_seen = aio.Event()

        assert set(self.entities.keys()) <= {
            BUTTON_DOMAIN,
            BINARY_SENSOR_DOMAIN,
            CLIMATE_DOMAIN,
            COVER_DOMAIN,
            DEVICE_TRACKER_DOMAIN,
            LIGHT_DOMAIN,
            SELECT_DOMAIN,
            SENSOR_DOMAIN,
            SWITCH_DOMAIN,
        }, f'Unknown domain: {list(self.entities.keys())}'

    def set_advertisement_seen(self):
        self._advertisement_seen.set()

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
        for postfix in [
            self.SET_POSTFIX,
            self.SET_POSITION_POSTFIX,
            self.SET_MODE_POSTFIX,
            self.SET_TARGET_TEMPERATURE_POSTFIX,
        ]:
            if topic.endswith(postfix):
                action_postfix = postfix
                topic = topic[:-len(postfix)]
                break
        return topic.strip('/'), action_postfix

    @property
    def subscribed_topics(self):
        postfix_domains = {
            self.SET_POSTFIX:
                [
                    BUTTON_DOMAIN, CLIMATE_DOMAIN, COVER_DOMAIN, LIGHT_DOMAIN,
                    SELECT_DOMAIN, SWITCH_DOMAIN,
                ],
            self.SET_POSITION_POSTFIX: [COVER_DOMAIN],
            self.SET_MODE_POSTFIX: [CLIMATE_DOMAIN],
            self.SET_TARGET_TEMPERATURE_POSTFIX: [CLIMATE_DOMAIN],
        }

        topics = []
        for postfix, domains in postfix_domains.items():
            topics.extend((
                '/'.join(filter(None, (
                    self.unique_id,
                    entity.get('topic', self.STATE_TOPIC),
                    postfix,
                )))
                for cls, items in self.entities.items()
                for entity in items
                if cls in domains
            ))
        return topics

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
    def friendly_id(self):
        # should be used in entity names in homeassistant
        return self.friendly_name or self.dev_id

    @property
    def unique_id(self):
        # name and manufacturer can change while working, e.g. when
        # a device sends his name. To avoid changing topics use
        # the ID based on mac address only
        return f'0x{self.dev_id}'

    @property
    def unique_name(self):
        # can change over time. Don't use it as an identifier
        parts = [self.manufacturer, self.model, self.friendly_id]
        return '_'.join([p.replace(' ', '_') for p in parts if p])

    @property
    def suggested_area(self):
        return self._suggested_area

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
    def availability_topic(self):
        return self._get_topic('availability')

    @property
    @abc.abstractmethod
    def entities(self) -> ty.Dict[str, ty.Any]:
        return {}

    @abc.abstractmethod
    def get_values_by_entities(self) -> ty.Dict[str, ty.Any]:
        pass

    async def send_availability(self, publish_topic, value: bool):
        await publish_topic(
            topic=self.availability_topic,
            value='online' if value else 'offline',
            nowait=True,
        )

    async def handle_messages(self, *args, **kwargs):
        while True:
            await aio.sleep(1)

    async def update_device_data(self, send_config):
        """
        Call this method on each iteration in handle.
        It will update rssi and config
        """
        if not self.config_sent:
            await send_config()
        if self.client:  # in passive mode, client is None
            # For newer bleak versions rssi is not accessible in
            # connection mode. Use previous values from scanning
            self.rssi = (await extract_rssi(self.client)) or self.rssi

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
        pass

    async def get_client(self, ble_device: ty.Optional[BLEDevice], **kwargs) \
            -> BleakClient:
        assert self.MAC_TYPE in ('public', 'random')
        client = BleakClient(
            ble_device or self.mac,
            address_type=self.MAC_TYPE,
            disconnected_callback=self._on_disconnect,
            **kwargs,
        )
        client.manager = await get_global_bluez_manager()
        return client

    async def _get_client_and_connect(self, adapter: str,
                                      ble_device: BLEDevice,
                                      timeout: int):
        client = await self.get_client(
            ble_device=ble_device,
            adapter=adapter,
        )
        self.disconnected_event.clear()

        await aio.wait_for(client.connect(), timeout=timeout)
        return client

    async def connect(self, adapter: str, ble_device: BLEDevice):
        if self.is_passive:
            return

        try:
            self.client = await aio.wait_for(
                self._get_client_and_connect(
                    adapter,
                    ble_device,
                    timeout=10,
                ),
                # 10 is the implicit timeout in bleak client, add 2 more seconds
                # for internal routines
                timeout=12,
            )
        except aio.TimeoutError as e:
            self.disconnected_event.set()
            raise ConnectionTimeoutError() from e
        except (Exception, aio.CancelledError):
            self.disconnected_event.set()
            raise
        self._advertisement_seen.clear()
        _LOGGER.info(f'Connected to {self.client.address}')

    def _on_disconnect(self, client, *args):
        _LOGGER.debug(f'Client {client.address} disconnected, device={self}')
        self.disconnected_event.set()

    async def close(self):
        try:
            connected = self.client and self.client.is_connected
        # exception on macos when checking for is_connected()
        except AttributeError:
            connected = True
        if connected:
            await self.client.disconnect()
        await super().close()

    @property
    def entities_with_lqi(self):
        sensor_entities = self.entities.get(SENSOR_DOMAIN, [])
        sensor_entities.append(
            {
                'name': 'linkquality',
                'unit_of_measurement': 'lqi',
                'icon': 'signal',
                'entity_category': 'diagnostic',
                **(
                    {'topic': self.LINKQUALITY_TOPIC}
                    if self.LINKQUALITY_TOPIC else {}
                ),
            },
        )
        return {
            **self.entities,
            SENSOR_DOMAIN: sensor_entities,
        }

    async def _notify_state(self, publish_topic):
        values_by_name = {
            'linkquality': self.linkquality,
            **self.get_values_by_entities(),
        }

        _LOGGER.info(f'[{self}] send state={values_by_name}')

        data_by_topic = defaultdict(dict)
        for domain, entities in self.entities_with_lqi.items():
            for entity in entities:
                name = entity['name']
                if name not in values_by_name:
                    continue

                value = values_by_name[name]
                content_values = (
                    value if isinstance(value, dict) else {name: value}
                )

                for parameter, val in content_values.items():
                    if domain in [SENSOR_DOMAIN, BINARY_SENSOR_DOMAIN]:
                        val = self.transform_value(val)
                    topic = self._get_topic_for_entity(entity)
                    data_by_topic[topic][parameter] = val
        coros = [
            publish_topic(topic=topic, value=json.dumps(values))
            for topic, values in data_by_topic.items()
        ]
        if coros:
            await aio.gather(*coros)
            self.initial_status_sent = True


class Sensor(Device, abc.ABC):
    # a list of state properties that must be not None at least one of them
    # to send data.
    # E.g. only battery updated, but wait for temperature and humidity
    REQUIRED_VALUES: ty.Sequence[str] = ()
    READ_DATA_IN_ACTIVE_LOOP: bool = False

    def __init__(self, mac, *args, **kwargs) -> None:
        super().__init__(mac, *args, **kwargs)
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

    def get_values_by_entities(self) -> ty.Dict[str, ty.Any]:
        state = {}
        if self._state is None:
            return {}

        if hasattr(self._state, 'as_dict'):
            state_dict = self._state.as_dict()
        else:
            state_dict = asdict(self._state)
        for domain, entities in self.entities.items():
            for entity in entities:
                sensor_name = entity['name']
                value = state_dict.get(sensor_name, None)
                if value is not None:
                    state[sensor_name] = self.transform_value(value)
        if self.REQUIRED_VALUES and not any(
            state.get(x) for x in self.REQUIRED_VALUES
        ):
            return {}
        return state

    async def do_active_loop(self, publish_topic):
        await self._notify_state(publish_topic)

    async def do_passive_loop(self, publish_topic):
        await self._notify_state(publish_topic)

    async def handle_active(self, publish_topic, send_config, *args, **kwargs):
        while True:
            await self.update_device_data(send_config)
            if not self.READ_DATA_IN_ACTIVE_LOOP and not self._state:
                await aio.sleep(self.NOT_READY_SLEEP_INTERVAL)
                continue

            await self.do_active_loop(publish_topic)
            if (
                self.ACTIVE_CONNECTION_MODE ==
                ConnectionMode.ACTIVE_POLL_WITH_DISCONNECT
            ):
                if self.client.is_connected:
                    await self.client.disconnect()
                # let DeviceManager.manage_device() handle reconnections
                return

            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

    async def handle_passive(self, publish_topic, send_config, *args, **kwargs):
        while True:
            if not self._state:
                await aio.sleep(self.NOT_READY_SLEEP_INTERVAL)
                continue

            await self.update_device_data(send_config)
            await self.do_passive_loop(publish_topic)
            await aio.sleep(self.passive_sleep_interval)

    async def handle(self, *args, **kwargs):
        if self.is_passive:
            return await self.handle_passive(*args, **kwargs)
        return await self.handle_active(*args, **kwargs)


@dataclass
class HumidityTemperatureSensorState:
    battery: int = 0
    temperature: float = 0
    humidity: float = 0


class HumidityTemperatureSensor(Sensor, abc.ABC):
    SENSOR_CLASS = HumidityTemperatureSensorState
    # send data only if temperature or humidity is set
    REQUIRED_VALUES = ('temperature', 'humidity')

    @property
    def entities(self):
        return {
            SENSOR_DOMAIN: [
                {
                    'name': 'temperature',
                    'device_class': 'temperature',
                    'unit_of_measurement': '\u00b0C',
                },
                {
                    'name': 'humidity',
                    'device_class': 'humidity',
                    'unit_of_measurement': '%',
                },
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                    'entity_category': 'diagnostic',
                },
            ],
        }


class SubscribeAndSetDataMixin:
    DATA_CHAR: uuid.UUID = None  # type: ignore
    SENSOR_CLASS: ty.Any = None  # type: ignore

    def filter_notifications(self, sender, data):
        return True

    def process_data(self, data: bytearray):
        self._state = self.SENSOR_CLASS.from_data(data)

    def notification_handler(self, sender, data: bytearray):
        _LOGGER.debug("{0} notification: {1}: {2}".format(
            self,
            sender,
            format_binary(data),
        ))
        if self.filter_notifications(sender, data):
            self.process_data(data)

    async def get_device_data(self):
        if self.DATA_CHAR:
            await self.client.start_notify(
                self.DATA_CHAR,
                self.notification_handler,
            )
        await super().get_device_data()


class CoverMovementType(Enum):
    STOP = 0
    POSITION = 1


class BaseCover(Device, abc.ABC):
    COVER_ENTITY = 'cover'

    # HA notation. We convert value on setting and receiving data
    CLOSED_POSITION = 0
    OPEN_POSITION = 100

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._state = namedtuple(
            'CoverState',
            (
                'position', 'target_position', 'run_state',
            ),
        )(position=0, target_position=0, run_state=CoverRunState.STOPPED)

    @property
    def entities(self):
        return {
            COVER_DOMAIN: [
                {
                    'name': self.COVER_ENTITY,
                    'topic': self.COVER_ENTITY,
                    'device_class': 'shade',
                },
            ],
            SENSOR_DOMAIN: [
                {
                    'name': 'battery',
                    'device_class': 'battery',
                    'unit_of_measurement': '%',
                },
                {
                    'name': 'illuminance',
                    'device_class': 'illuminance',
                    'unit_of_measurement': 'lx',
                },
            ],
        }

    @abc.abstractmethod
    async def _stop(self):
        pass

    @abc.abstractmethod
    async def _set_position(self, value):
        pass

    @abc.abstractmethod
    async def _update_running_state(self):
        """ This method is called as a short update while
        opening the shades"""

    @abc.abstractmethod
    async def _update_full_state(self):
        """ This method is called to refetch all values from the device"""

    async def _do_movement(self, movement_type: CoverMovementType,
                           target_position: ty.Optional[int]):
        if movement_type == CoverMovementType.POSITION and \
                target_position is not None:
            if self.CLOSED_POSITION <= target_position <= self.OPEN_POSITION:
                await self._set_position(target_position)
                if self._state.position > target_position:
                    self._state.target_position = target_position
                    self._state.run_state = CoverRunState.CLOSING
                elif self._state.position < target_position:
                    self._state.target_position = target_position
                    self._state.run_state = CoverRunState.OPENING
                else:
                    self._state.target_position = None
                    if target_position == self.OPEN_POSITION:
                        self._state.run_state = CoverRunState.OPEN
                    elif target_position == self.CLOSED_POSITION:
                        self._state.run_state = CoverRunState.CLOSED
                    else:
                        self._state.run_state = CoverRunState.STOPPED
            else:
                _LOGGER.error(
                    f'[{self}] Incorrect position value: '
                    f'{repr(target_position)}',
                )
        else:
            await self._stop()
            self._state.run_state = CoverRunState.STOPPED

    async def _handle_message(self, message, publish_topic):
        value = message['value']
        entity_topic, action_postfix = self.get_entity_subtopic_from_topic(
            message['topic'],
        )
        if entity_topic == self._get_topic_for_entity(
                self.get_entity_by_name(COVER_DOMAIN, self.COVER_ENTITY),
                skip_unique_id=True,
        ):
            value = self.transform_value(value)
            target_position = None
            if action_postfix == self.SET_POSTFIX:
                _LOGGER.info(
                    f'[{self}] set mode {entity_topic} to "{value}"',
                )
                if value.lower() == 'open':
                    movement_type = CoverMovementType.POSITION
                    target_position = self.OPEN_POSITION
                elif value.lower() == 'close':
                    movement_type = CoverMovementType.POSITION
                    target_position = self.CLOSED_POSITION
                else:
                    movement_type = CoverMovementType.STOP
            elif action_postfix == self.SET_POSITION_POSTFIX:
                movement_type = CoverMovementType.POSITION
                _LOGGER.info(
                    f'[{self}] set position {entity_topic} to "{value}"',
                )
                try:
                    target_position = int(value)
                except ValueError:
                    pass
            else:
                _LOGGER.warning(
                    f'[{self}] unknown action postfix {action_postfix}',
                )
                return False

            while True:
                try:
                    await self._do_movement(movement_type, target_position)
                    await self._notify_state(publish_topic)
                    break
                except ConnectionError as e:
                    _LOGGER.exception(str(e))
                await aio.sleep(5)
            return True

    async def handle_messages(self, publish_topic, *args, **kwargs):
        while True:
            try:
                if not self.client.is_connected:
                    raise ConnectionError()
                message = await aio.wait_for(
                    self.message_queue.get(),
                    timeout=60,
                )
            except aio.TimeoutError:
                await aio.sleep(1)
                continue
            await self._handle_message(message, publish_topic)


class ClimateMode(Enum):
    OFF = 'off'
    HEAT = 'heat'


class BaseClimate(Device, abc.ABC):
    CLIMATE_ENTITY = 'climate'
    MODES: ty.Iterable[ClimateMode] = ()

    @property
    def entities(self):
        return {
            CLIMATE_DOMAIN: [
                {
                    'name': self.CLIMATE_ENTITY,
                    'modes': [x.value for x in self.MODES],
                },
            ],
        }

    @abc.abstractmethod
    async def _set_target_temperature(self, value):
        pass

    @abc.abstractmethod
    async def _switch_mode(self, next_mode):
        pass

    async def _handle_message(self, message, publish_topic):
        value = message['value']
        entity_topic, action_postfix = self.get_entity_subtopic_from_topic(
            message['topic'],
        )
        if entity_topic == self._get_topic_for_entity(
                self.get_entity_by_name(CLIMATE_DOMAIN, self.CLIMATE_ENTITY),
                skip_unique_id=True,
        ):
            if action_postfix == self.SET_MODE_POSTFIX:
                _LOGGER.info(
                    f'[{self}] set mode {entity_topic} to "{value}"',
                )
                try:
                    value = ClimateMode(value.lower())
                except ValueError:
                    _LOGGER.warning(f"{self} Incorrect mode {value}")
                    return False
                else:
                    if value not in self.MODES:
                        _LOGGER.warning(f"{self} Incorrect mode {value}")
                        return False
                state_change_coro = self._switch_mode(value)
            elif action_postfix == self.SET_TARGET_TEMPERATURE_POSTFIX:
                try:
                    target_temperature = float(value)
                except ValueError:
                    _LOGGER.exception("Incorrect temperature")
                    return False
                _LOGGER.info(
                    f'[{self}] set temperature {entity_topic} to "{value}"',
                )
                state_change_coro = \
                    self._set_target_temperature(target_temperature)
            else:
                _LOGGER.warning(
                    f'[{self}] unknown action postfix {action_postfix}',
                )
                return False

            if not state_change_coro:
                return False

            while True:
                try:
                    await state_change_coro
                    await self._notify_state(publish_topic)
                    break
                except ConnectionError as e:
                    _LOGGER.exception(str(e))
                await aio.sleep(5)
            return True

    async def handle_messages(self, publish_topic, *args, **kwargs):
        while True:
            try:
                if not self.client.is_connected:
                    raise ConnectionError()
                message = await aio.wait_for(
                    self.message_queue.get(),
                    timeout=60,
                )
            except aio.TimeoutError:
                await aio.sleep(1)
                continue
            await self._handle_message(message, publish_topic)

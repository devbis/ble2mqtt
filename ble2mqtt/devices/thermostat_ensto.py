import asyncio as aio
import logging
import typing as ty
import uuid
from dataclasses import dataclass

from ble2mqtt.protocols.ensto import ActiveMode, EnstoProtocol, Measurements

from ..utils import format_binary
from .base import (
    BINARY_SENSOR_DOMAIN,
    SENSOR_DOMAIN,
    BaseClimate,
    ClimateMode,
    ConnectionMode,
)
from .uuids import DEVICE_NAME, SOFTWARE_VERSION

_LOGGER = logging.getLogger(__name__)

UUID_CHILD_LOCK = uuid.UUID('6e3064e2-d9a5-4ca0-9d14-017c59627330')
UUID_MEASUREMENTS = uuid.UUID('66ad3e6b-3135-4ada-bb2b-8b22916b21d4')
UUID_VACATION = uuid.UUID('6584e9c6-4784-41aa-ac09-c899191048ae')
UUID_DATE = uuid.UUID('b43f918a-b084-45c8-9b60-df648c4a4a1e')
UUID_HEATING_POWER = uuid.UUID('53b7bf87-6cf0-4790-839a-e72d3afbec44')
UUID_FACTORY_RESET = uuid.UUID('f366dddb-ebe2-43ee-83c0-472ded74c8fa')


RELAY_ENTITY = 'relay'
TARGET_TEMPERATURE_ENTITY = 'target_temperature'
FLOOR_TEMPERATURE_ENTITY = 'floor_temperature'
ROOM_TEMPERATURE_ENTITY = 'room_temperature'


@dataclass
class EnstoState:
    mode: ClimateMode = ClimateMode.OFF
    temperature: ty.Optional[float] = None
    target_temperature: ty.Optional[float] = None
    floor_temperature: ty.Optional[float] = None
    room_temperature: ty.Optional[float] = None
    relay_is_on: bool = False
    target_temperature_with_offset: ty.Optional[float] = None


class EnstoThermostat(EnstoProtocol, BaseClimate):
    NAME = 'ensto_thermostat'  # EPHBEBT
    MANUFACTURER = 'Ensto'
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_KEEP_CONNECTION

    AUTH_CHAR = UUID_FACTORY_RESET
    DATE_CHAR = UUID_DATE
    VACATION_CHAR = UUID_VACATION
    MEASUREMENTS_CHAR = UUID_MEASUREMENTS
    CUSTOM_MEMORY_SLOT_CHAR = UUID_HEATING_POWER
    DEFAULT_TARGET_TEMPERATURE = 18.0
    MIN_POTENTIOMETER_VALUE = 5.0

    SEND_DATA_PERIOD = 60

    MODES = (ClimateMode.OFF, ClimateMode.HEAT)

    def __init__(self, *args, key='', **kwargs):
        super().__init__(*args, **kwargs)
        self._state = EnstoState()
        if key:
            assert len(key) == 8, f'{self}: Key must be 8 chars long'
            self._reset_id = bytes.fromhex(key)
        self.initial_status_sent = False

    @property
    def entities(self):
        return {
            **super().entities,
            BINARY_SENSOR_DOMAIN: [
                {
                    'name': RELAY_ENTITY,
                    'device_class': 'power',
                    'entity_category': 'diagnostic',
                }
            ],
            SENSOR_DOMAIN: [
                {
                    'name': TARGET_TEMPERATURE_ENTITY,
                    'device_class': 'temperature',
                    'unit_of_measurement': '\u00b0C',
                },
                {
                    'name': FLOOR_TEMPERATURE_ENTITY,
                    'device_class': 'temperature',
                    'unit_of_measurement': '\u00b0C',
                },
                {
                    'name': ROOM_TEMPERATURE_ENTITY,
                    'device_class': 'temperature',
                    'unit_of_measurement': '\u00b0C',
                }
            ],
        }

    async def get_device_data(self):
        await super().get_device_data()
        _LOGGER.debug(f'{self} start protocol')
        await self.protocol_start()
        name = await self._read_with_timeout(DEVICE_NAME)
        if isinstance(name, (bytes, bytearray)):
            self._model = name[1:].decode('latin1').strip(' \0') or 'Heater'
        version = await self.client.read_gatt_char(SOFTWARE_VERSION)
        if version:
            self._version = version.decode('latin1')
        _LOGGER.debug(f'{self} name: {self._model}, version: {self._version}')

        self._state.target_temperature = await self.read_target_temp()
        _LOGGER.debug(f'{self} target temp: {self._state}')
        await self.guess_potentiometer_position()
        await self._update_state()
        previously_saved_target_temp = await self.read_target_temp()
        if previously_saved_target_temp:
            self._state.target_temperature = previously_saved_target_temp

    async def guess_potentiometer_position(self):
        _LOGGER.debug(f'{self} guess_potentiometer_position')
        values = await self.read_measurements()
        cur_vacation_mode = await self.read_vacation_mode()
        self.set_current_potentiometer_value(values, cur_vacation_mode)
        _LOGGER.debug(
            f'{self} update _heater_potentiometer_temperature '
            f'{self._heater_potentiometer_temperature}',
        )

    async def _set_target_temperature(self, value):
        await self.save_target_temp(value)
        # set vacation mode and offset
        if self._state.mode != ClimateMode.OFF:
            await self.set_vacation_mode(value)
        self._state.target_temperature = value
        await self._update_state()

    async def _switch_mode(self, next_mode: ClimateMode):
        # set vacation mode and offset
        if next_mode == ClimateMode.HEAT:
            temp = await self.read_target_temp()
            if not temp:
                temp = self.DEFAULT_TARGET_TEMPERATURE
                await self.save_target_temp(temp)
        else:
            temp = 5.0
        await self.set_vacation_mode(temp, True)
        await self._update_state()

    def get_values_by_entities(self) -> ty.Dict[str, ty.Any]:
        return {
            self.CLIMATE_ENTITY: {
                'mode': self._state.mode.value,
                'temperature': self._state.temperature,
                'target_temperature': self._state.target_temperature,
            },
            RELAY_ENTITY: {
                'relay': 'ON' if self._state.relay_is_on else 'OFF',
            },
            TARGET_TEMPERATURE_ENTITY: {
                'target_temperature': self._state.target_temperature,
            },
            FLOOR_TEMPERATURE_ENTITY: {
                'floor_temperature': self._state.floor_temperature,
            },
            ROOM_TEMPERATURE_ENTITY: {
                'room_temperature': self._state.room_temperature,
            }
        }

    def set_current_potentiometer_value(self, measurements: Measurements,
                                        vacation_data: bytes):
        offset_temp = int.from_bytes(
            vacation_data[10:12],
            byteorder='little',
            signed=True,
        )/100
        vacation_enabled = vacation_data[13]
        _LOGGER.debug(
            f'{self} vacation_data: '
            f'offset_temp={offset_temp}, vacation_enabled={vacation_enabled}',
        )
        if measurements.active_mode == ActiveMode.MANUAL:
            self._heater_potentiometer_temperature = \
                measurements.target_temperature
        elif measurements.active_mode == ActiveMode.VACATION:
            self._heater_potentiometer_temperature = \
                measurements.target_temperature - offset_temp

    async def _update_state(self):
        values = await self.read_measurements()
        _LOGGER.debug(f'{self} parsed measurements: {values}')
        self._state.temperature = values.temperature
        self._state.target_temperature_with_offset = values.target_temperature
        self._state.floor_temperature = values.floor_temperature
        self._state.room_temperature = values.room_temperature
        self._state.relay_is_on = values.relay_is_on
        vacation_data = await self.read_vacation_mode()
        _LOGGER.debug(f'{self} vacation_data: {format_binary(vacation_data)}')
        self.set_current_potentiometer_value(values, vacation_data)

        if (
            values.active_mode != ActiveMode.VACATION or
            self._state.target_temperature_with_offset >
                self.MIN_POTENTIOMETER_VALUE
        ):
            self._state.mode = ClimateMode.HEAT
            self._state.target_temperature = \
                self._state.target_temperature_with_offset
        else:
            self._state.mode = ClimateMode.OFF

    async def handle(self, publish_topic, send_config, *args, **kwargs):
        timer = 0
        while True:
            await self.update_device_data(send_config)

            timer += self.ACTIVE_SLEEP_INTERVAL
            if not self.initial_status_sent or \
                    timer >= self.SEND_DATA_PERIOD:
                _LOGGER.debug(f'[{self}] check for measurements')
                await self._update_state()
                await self._notify_state(publish_topic)
                timer = 0
            await aio.sleep(self.ACTIVE_SLEEP_INTERVAL)

import asyncio as aio
import json
import logging
import struct
import uuid
from dataclasses import dataclass

from ble2mqtt.devices.base import SENSOR_DOMAIN, ConnectionMode, Sensor
from ble2mqtt.devices.uuids import DEVICE_NAME
from ble2mqtt.protocols.wp6003 import WP6003Protocol

_LOGGER = logging.getLogger(__name__)

TX_CHAR = uuid.UUID('0000fff1-0000-1000-8000-00805f9b34fb')
RX_CHAR = uuid.UUID('0000fff4-0000-1000-8000-00805f9b34fb')


@dataclass
class SensorState:
    FORMAT = '>6B6H'

    temperature: float = 0.0
    tvoc: float = 0.0
    hcho: float = 0.0
    co2: int = 0

    @classmethod
    def from_bytes(cls, response):
        # 0a 15 02 0e 09 1e 00d4 0800 0007 0000 0100 0230
        (
            header,  # 0
            year,  # 1
            month,  # 2
            day,  # 3
            hour,  # 4
            minute,  # 5
            temp,  # 6-7
            _,  # 8-9
            tvoc,  # 10-11
            hcho,  # 12-13
            _,  # 14-15
            co2,  # 16-17
        ) = struct.unpack(cls.FORMAT, response)
        if header != 0x0a:
            raise ValueError('Bad response')
        if tvoc == hcho == 0x3fff:
            raise ValueError('Bad value')
        return cls(
            temperature=temp/10,
            tvoc=tvoc/1000,
            hcho=hcho/1000,
            co2=co2,
        )


class VsonWP6003(WP6003Protocol, Sensor):
    NAME = 'wp6003'
    RX_CHAR = RX_CHAR
    TX_CHAR = TX_CHAR
    ACTIVE_SLEEP_INTERVAL = 20
    MANUFACTURER = 'Vson'
    ACTIVE_CONNECTION_MODE = ConnectionMode.ACTIVE_KEEP_CONNECTION
    READ_DATA_IN_ACTIVE_LOOP = True

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
                    'name': 'tvoc',
                    'unit_of_measurement': 'mg/m³',
                    'icon': 'air-filter',
                },
                {
                    'name': 'hcho',
                    'unit_of_measurement': 'mg/m³',
                    'icon': 'air-filter',
                },
                {
                    'name': 'co2',
                    'unit_of_measurement': 'ppm',
                    'icon': 'molecule-co2',
                },
            ],
        }

    async def read_state(self):
        response = await self.read_value()
        for _ in range(5):
            try:
                self._state = SensorState.from_bytes(response)
            except ValueError as e:
                _LOGGER.warning(f'{self} {repr(e)}')
                await aio.sleep(1)
            else:
                break

    async def get_device_data(self):
        # ignore reading firmware version, it doesn't support it
        name = await self._read_with_timeout(DEVICE_NAME)
        if isinstance(name, (bytes, bytearray)):
            self._model = name.decode().strip('\0')
        await self.protocol_start()
        await self.send_reset()
        await self.write_time()

    async def do_active_loop(self, publish_topic):
        try:
            await aio.wait_for(self.read_state(), 5)
            await self._notify_state(publish_topic)
        except (aio.CancelledError, KeyboardInterrupt):
            raise
        except Exception:
            _LOGGER.exception(f'{self} problem with reading values')

    async def _notify_state(self, publish_topic):
        _LOGGER.info(f'[{self}] send state={self._state}')
        state = self.get_entity_map()
        if state:
            state['linkquality'] = self.linkquality
            await publish_topic(
                topic=self._get_topic(self.STATE_TOPIC),
                value=json.dumps(state),
            )

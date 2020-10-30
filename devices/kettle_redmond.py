import asyncio as aio
import logging
import uuid

from bleak import BleakClient

from protocols.redmond import Kettle200State, Mode, RedmondKettle200Protocol

from .base import Device

logger = logging.getLogger(__name__)

UUID_NORDIC_TX = uuid.UUID("6e400002-b5a3-f393-e0a9-e50e24dcca9e")
UUID_NORDIC_RX = uuid.UUID("6e400003-b5a3-f393-e0a9-e50e24dcca9e")


class RedmondKettle(RedmondKettle200Protocol, Device):
    NAME = 'redmond200'
    TX_CHAR = UUID_NORDIC_TX
    RX_CHAR = UUID_NORDIC_RX

    def __init__(self, mac, model, key=b'\xff\xff\xff\xff\xff\xff\xff\xff'):
        super().__init__()
        assert isinstance(key, bytes) and len(key) == 8
        self._mac = mac
        self._key = key
        self._model = model
        self._version = None
        self._state = None
        self.protocol_init(client=BleakClient(mac, address_type='random'))

    async def init(self):
        await super().start()
        await self.login(self._key)
        version = await self.get_version()
        if version:
            self._version = f'{version[0]}.{version[1]}'
        state = await self.get_mode()
        if state:
            self._state = state

    async def handle(self, period=60):
        while True:
            # TODO: send parameters
            await aio.sleep(period)

    @property
    def dev_id(self):
        return self._mac.replace(':', '').lower()

    @property
    def manufacturer(self):
        return 'Redmond'

    @property
    def model(self):
        return self._model

    @property
    def version(self):
        return self._version

    @property
    def entities(self):
        return {
            'switch': [
                {
                    'name': 'kettle',
                    'icon': 'kettle',
                },
            ],
        }

    async def process_topic(self, topic: str, value, *args, **kwargs):
        publish_topic = kwargs['publish_topic']
        entity_name = self.get_entity_from_topic(topic)
        if entity_name == 'kettle':
            value = self.transform_value(value)
            logger.info(f'[{self._mac}] switch kettle {entity_name=} {value=}')
            if value == 'ON':
                await self.set_mode(Kettle200State(
                    mode=Mode.BOIL,
                ))
                await self.run()
            elif value == 'OFF':
                await self.stop()
            await publish_topic(
                topic='/'.join((self.unique_id, entity_name)),
                value=self.transform_value(value),
            )

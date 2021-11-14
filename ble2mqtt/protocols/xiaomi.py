import abc
import asyncio as aio
import logging
import uuid

from ..devices.base import Sensor, SubscribeAndSetDataMixin

_LOGGER = logging.getLogger(__name__)


# Xiaomi Humidity/Temperature sensors

class XiaomiPoller(SubscribeAndSetDataMixin, Sensor, abc.ABC):
    DATA_CHAR: uuid.UUID = None  # type: ignore
    BATTERY_CHAR: uuid.UUID = None  # type: ignore
    MANUFACTURER = 'Xiaomi'

    def __init__(self, *args, loop, **kwargs):
        super().__init__(*args, loop=loop, **kwargs)
        self._stack = aio.LifoQueue(loop=loop)

    def process_data(self, data):
        self._loop.call_soon_threadsafe(self._stack.put_nowait, data)

    async def read_and_send_data(self, publish_topic):
        raise NotImplementedError()

    async def handle_active(self, publish_topic, send_config, *args, **kwargs):
        _LOGGER.debug(f'Wait {self} for connecting...')
        sec_to_wait_connection = 0
        while True:
            if not self.client.is_connected:
                if sec_to_wait_connection >= 30:
                    raise TimeoutError(
                        f'{self} not connected for 30 sec in handle()',
                    )
                sec_to_wait_connection += self.NOT_READY_SLEEP_INTERVAL
                await aio.sleep(self.NOT_READY_SLEEP_INTERVAL)
                continue
            try:
                _LOGGER.debug(f'{self} connected!')
                # in case of bluetooth error populating queue
                # could stop and will wait for self._stack.get() forever
                await self.update_device_data(send_config)
                await aio.wait_for(
                    self.read_and_send_data(publish_topic),
                    timeout=15,
                )
            except ValueError as e:
                _LOGGER.error(f'[{self}] Cannot read values {str(e)}')
            else:
                await self.close()
                return
            await aio.sleep(1)


# Xiaomi Kettles

class XiaomiCipherMixin:
    # Picked from the https://github.com/drndos/mikettle/
    @staticmethod
    def generate_random_token() -> bytes:
        return bytes([  # from component, maybe random is ok
            0x01, 0x5C, 0xCB, 0xA8, 0x80, 0x0A, 0xBD, 0xC1, 0x2E, 0xB8,
            0xED, 0x82,
        ])
        # return os.urandom(12)

    @staticmethod
    def reverse_mac(mac) -> bytes:
        parts = mac.split(":")
        reversed_mac = bytearray()
        length = len(parts)
        for i in range(1, length + 1):
            reversed_mac.extend(bytearray.fromhex(parts[length - i]))
        return reversed_mac

    @staticmethod
    def mix_a(mac, product_id) -> bytes:
        return bytes([
            mac[0], mac[2], mac[5], (product_id & 0xff), (product_id & 0xff),
            mac[4], mac[5], mac[1],
        ])

    @staticmethod
    def mix_b(mac, product_id) -> bytes:
        return bytes([
            mac[0], mac[2], mac[5], ((product_id >> 8) & 0xff), mac[4], mac[0],
            mac[5], (product_id & 0xff),
        ])

    @staticmethod
    def _cipher_init(key) -> bytes:
        perm = bytearray()
        for i in range(0, 256):
            perm.extend(bytes([i & 0xff]))
        keyLen = len(key)
        j = 0
        for i in range(0, 256):
            j += perm[i] + key[i % keyLen]
            j = j & 0xff
            perm[i], perm[j] = perm[j], perm[i]
        return perm

    @staticmethod
    def _cipher_crypt(input, perm) -> bytes:
        index1 = 0
        index2 = 0
        output = bytearray()
        for i in range(0, len(input)):
            index1 = index1 + 1
            index1 = index1 & 0xff
            index2 += perm[index1]
            index2 = index2 & 0xff
            perm[index1], perm[index2] = perm[index2], perm[index1]
            idx = perm[index1] + perm[index2]
            idx = idx & 0xff
            output_byte = input[i] ^ perm[idx]
            output.extend(bytes([output_byte & 0xff]))

        return output

    @classmethod
    def cipher(cls, key, input) -> bytes:
        perm = cls._cipher_init(key)
        return cls._cipher_crypt(input, perm)

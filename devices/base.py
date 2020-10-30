registered_device_types = {}


class RegisteredType(type):
    def __new__(cls, clsname, superclasses, attributedict):
        newclass = type.__new__(cls, clsname, superclasses, attributedict)
        # condition to prevent base class registration
        if superclasses:
            assert newclass.NAME is not None
            registered_device_types[newclass.NAME] = newclass
        return newclass


class Device(metaclass=RegisteredType):
    MQTT_VALUES = None
    ON_OFF = False
    SET_POSTFIX = 'set'
    NAME = None

    def get_entity_from_topic(self, topic: str):
        return topic.removesuffix(self.SET_POSTFIX).removeprefix(
            self.unique_id,
        ).strip('/')

    @staticmethod
    def transform_value(value):
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
        return None

    @property
    def model(self):
        return None

    @property
    def dev_id(self):
        return None

    @property
    def version(self):
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

from enum import Enum


class DescriptorUUID(Enum):
    characteristic_extended_properties = 0x2900
    characteristic_user_description = 0x2901
    client_characteristic_configuration = 0x2902
    server_characteristic_configuration = 0x2903
    characteristic_presentation_format = 0x2904
    characteristic_aggregate_format = 0x2905
    valid_range = 0x2906
    external_report_reference = 0x2907
    report_reference = 0x2908
    es_configuration = 0x290b
    es_measurement = 0x290c
    es_trigger_setting = 0x290d

    def as_uuid(self):
        return '%08x-0000-1000-8000-00805f9b34fb' % self.value

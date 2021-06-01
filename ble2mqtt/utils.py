MAX_RSSI = 0
MIN_RSSI = -100


def format_binary(data: bytes, delimiter=' '):
    return delimiter.join(format(x, '02x') for x in data)


def cr2032_voltage_to_percent(mvolts: int):
    coeff = 0.8  # >2.9V counts as 100% = (2900 - 2100)/100
    return max(min(int(round((mvolts/1000 - 2.1)/coeff, 2) * 100), 100), 0)


def rssi_to_linkquality(rssi):
    return min(int(round(255 * (rssi - MIN_RSSI) / (MAX_RSSI - MIN_RSSI))), 0)

def cr2032_voltage_to_percent(mvolts: int):
    return min(int(round((mvolts/1000 - 2.1), 2) * 100), 100)

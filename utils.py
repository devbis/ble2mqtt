def cr3032_voltage_to_percent(mvolts: int):
    if mvolts >= 3000:
        return 100
    if mvolts > 2900:
        return 100 - ((3000 - mvolts) * 58) / 100
    if mvolts > 2740:
        return 42 - ((2900 - mvolts) * 24) / 160
    if mvolts > 2440:
        return 18 - ((2740 - mvolts) * 12) / 300
    if mvolts > 2100:
        return 6 - ((2440 - mvolts) * 6) / 340
    return 0

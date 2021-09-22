from .bluepy import client, scanner


def add_bluepy_backend():
    import bleak

    print('PATCH BLEAK')
    bleak.BleakScanner = scanner.BleakScannerBluePy
    bleak.BleakClient = client.BleakClientBluePy
    bleak.discover = scanner.BleakScannerBluePy.discover

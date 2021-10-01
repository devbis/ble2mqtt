from .gattlib import client, scanner


def add_gattlib_backend():
    import bleak

    print('PATCH BLEAK')
    bleak.BleakScanner = scanner.BleakScannerGattlib
    bleak.BleakClient = client.BleakClientGattlib
    bleak.discover = scanner.BleakScannerGattlib.discover

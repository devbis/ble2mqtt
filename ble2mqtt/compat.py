import sys

import bleak


def get_loop_param(loop):
    if sys.version_info >= (3, 8):
        return {}
    return {'loop': loop}


def get_scanner(detection_callback) -> bleak.BleakScanner:
    if bleak.__version__ < '0.18':
        scanner = bleak.BleakScanner()
        scanner.register_detection_callback(detection_callback)
    else:
        scanner = bleak.BleakScanner(detection_callback=detection_callback)

    return scanner

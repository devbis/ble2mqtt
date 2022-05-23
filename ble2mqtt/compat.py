import sys


def get_loop_param(loop):
    if sys.version_info >= (3, 8):
        return {}
    return {'loop': loop}

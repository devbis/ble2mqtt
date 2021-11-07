from typing import Tuple

MAX_RSSI = 0
MIN_RSSI = -100


def format_binary(data: bytes, delimiter=' '):
    return delimiter.join(format(x, '02x') for x in data)


def cr2032_voltage_to_percent(mvolts: int):
    coeff = 0.8  # >2.9V counts as 100% = (2900 - 2100)/100
    return max(min(int(round((mvolts/1000 - 2.1)/coeff, 2) * 100), 100), 0)


def rssi_to_linkquality(rssi):
    return max(int(round(255 * (rssi - MIN_RSSI) / (MAX_RSSI - MIN_RSSI))), 0)


# code from home assisstant

def _match_max_scale(input_colors, output_colors):
    """Match the maximum value of the output to the input."""
    max_in = max(input_colors)
    max_out = max(output_colors)
    if max_out == 0:
        factor = 0.0
    else:
        factor = max_in / max_out
    return tuple(int(round(i * factor)) for i in output_colors)


def color_rgb_to_rgbw(r: int, g: int, b: int) -> Tuple[int, int, int, int]:
    """Convert an rgb color to an rgbw representation."""
    # Calculate the white channel as the minimum of input rgb channels.
    # Subtract the white portion from the remaining rgb channels.
    w = min(r, g, b)
    rgbw = (r - w, g - w, b - w, w)

    # Match the output maximum value to the input. This ensures the full
    # channel range is used.
    return _match_max_scale((r, g, b), rgbw)  # type: ignore


def color_rgbw_to_rgb(r: int, g: int, b: int, w: int) -> Tuple[int, int, int]:
    """Convert an rgbw color to an rgb representation."""
    # Add the white channel to the rgb channels.
    rgb = (r + w, g + w, b + w)

    # Match the output maximum value to the input. This ensures the
    # output doesn't overflow.
    return _match_max_scale((r, g, b, w), rgb)  # type: ignore

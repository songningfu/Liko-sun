import colorsys
import re
import secrets


HEX_COLOR_PATTERN = re.compile(r"^#[0-9A-Fa-f]{6}$")


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#" + "".join(f"{channel:02X}" for channel in rgb)


def _rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))  # type: ignore[return-value]


def _mix(value: str, target: tuple[int, int, int], amount: float) -> str:
    source = _rgb(value)
    return _hex(
        tuple(round(channel + (target[index] - channel) * amount) for index, channel in enumerate(source))  # type: ignore[arg-type]
    )


def _distance(first: str, second: str) -> float:
    a, b = _rgb(first), _rgb(second)
    return sum((a[index] - b[index]) ** 2 for index in range(3)) ** 0.5


def _relative_luminance(value: str) -> float:
    channels = []
    for channel in _rgb(value):
        normalized = channel / 255
        channels.append(normalized / 12.92 if normalized <= 0.04045 else ((normalized + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def random_srgb_color() -> str:
    """Return one uniformly sampled color from the full 24-bit sRGB space."""

    return f"#{secrets.randbelow(1 << 24):06X}"


def create_liko_colors() -> tuple[str, str]:
    shell = random_srgb_color()
    skin = random_srgb_color()
    while _distance(shell, skin) < 72:
        skin = random_srgb_color()
    return shell, skin


def validate_liko_colors(shell: str, skin: str) -> tuple[str, str]:
    """Validate a user-selected palette against the same readability rule as random colors."""

    if not HEX_COLOR_PATTERN.fullmatch(shell) or not HEX_COLOR_PATTERN.fullmatch(skin):
        raise ValueError("颜色格式不正确")
    shell, skin = shell.upper(), skin.upper()
    if _distance(shell, skin) < 72:
        raise ValueError("外壳色和肤色太接近，请换一组更容易分辨的颜色")
    return shell, skin


def _tone_variants(value: str) -> tuple[str, str]:
    red, green, blue = _rgb(value)
    hue, lightness, saturation = colorsys.rgb_to_hls(red / 255, green / 255, blue / 255)
    light_amount = 0.38 if lightness < 0.45 else 0.2
    deep_amount = 0.32 if lightness > 0.55 else 0.2
    return _mix(value, (255, 255, 255), light_amount), _mix(value, (0, 0, 0), deep_amount)


def derive_palette(shell: str, skin: str) -> dict[str, str]:
    shell_light, shell_deep = _tone_variants(shell)
    skin_light, skin_deep = _tone_variants(skin)
    face = "#FFFDF8" if _relative_luminance(skin) < 0.32 else "#30272C"
    return {
        "shell": shell.upper(),
        "shellLight": shell_light,
        "shellDeep": shell_deep,
        "skin": skin.upper(),
        "skinLight": skin_light,
        "skinDeep": skin_deep,
        "face": face,
    }


def format_liko_number(number: int) -> str:
    return f"No. {number:06d}"

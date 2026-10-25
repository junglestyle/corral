import re

_PART = re.compile(r"(\d+(?:\.\d+)?)([smhd])")
_UNIT = {"s": 1, "m": 60, "h": 3600, "d": 86400}


def parse_duration(text: str) -> float:
    """Seconds in "90s", "30m", "2h", "1h30m", or a bare number of seconds."""
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    if not re.fullmatch(f"(?:{_PART.pattern})+", text):
        raise ValueError(f"not a duration: {text!r}")
    return sum(float(n) * _UNIT[u] for n, u in _PART.findall(text))

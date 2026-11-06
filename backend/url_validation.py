"""URL validators for trusted catalog links and explicit browser origins."""

from urllib.parse import urlparse


def _valid_authority(value: str) -> bool:
    if not value or value != value.strip() or any(character.isspace() for character in value):
        return False
    try:
        parsed = urlparse(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return False
    return (
        bool(hostname)
        and "*" not in hostname
        and not parsed.netloc.endswith(":")
        and (port is None or 1 <= port <= 65535)
    )


def is_absolute_https_url(value: object) -> bool:
    if not isinstance(value, str) or not _valid_authority(value):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and parsed.username is None and parsed.password is None


def is_http_origin(value: object) -> bool:
    if not isinstance(value, str) or not _valid_authority(value):
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.username is None
        and parsed.password is None
        and not parsed.path
        and not parsed.query
        and not parsed.fragment
    )

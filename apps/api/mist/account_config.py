"""Configuration for individually managed MIST Atlas accounts."""

import base64
import binascii
from ipaddress import ip_address
import os
import re
from urllib.parse import urlsplit


def managed_mode():
    mode = os.getenv("MIST_ACCOUNT_MODE", "sandbox")
    if mode not in {"sandbox", "managed"}:
        raise RuntimeError("MIST_ACCOUNT_MODE must be sandbox or managed.")
    return mode == "managed"


def managed_origin():
    value = os.getenv("MIST_PUBLIC_ORIGIN", "")
    try:
        url = urlsplit(value)
        port = url.port
    except ValueError:
        raise RuntimeError("MIST_PUBLIC_ORIGIN must be a public HTTPS origin.") from None
    if (
        url.scheme != "https"
        or not url.hostname
        or not valid_hostname(url.hostname)
        or url.username is not None
        or url.password is not None
        or url.path not in {"", "/"}
        or url.query
        or url.fragment
        or re.search(r"[\s\\?#]", value)
        or port == 0
        or url.netloc.endswith(":")
    ):
        raise RuntimeError("MIST_PUBLIC_ORIGIN must be a public HTTPS origin.")
    return f"https://{url.netloc}"


def valid_hostname(hostname):
    try:
        ip_address(hostname)
    except ValueError:
        return len(hostname) <= 253 and all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in hostname.split(".")
        )
    return "%" not in hostname


def signing_key():
    value = os.getenv("MIST_ACCOUNT_TOKEN_KEY", "")
    try:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,512}", value):
            raise ValueError
        key = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
        if len(key) < 32 or len(set(key)) < 16 or base64.urlsafe_b64encode(key).decode().rstrip("=") != value:
            raise ValueError
    except (ValueError, binascii.Error):
        raise RuntimeError("MIST_ACCOUNT_TOKEN_KEY must be at least 32 random bytes encoded as unpadded base64url.") from None
    return key

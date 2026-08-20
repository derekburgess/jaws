"""Canonical, secret-free JSON primitives for stable identities."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .identifiers import CanonicalDigest, Identifier
from .secrets import REDACTED, Secret
from .time import utc_text

_SENSITIVE_KEY = re.compile(
    r"(?:^|[_-])(api[_-]?key|credential|password|private[_-]?key|secret|token)(?:$|[_-])",
    re.IGNORECASE,
)
_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(api[_-]?key|authorization|credential|password|secret|token)\b"
    r"(\s*[:=]\s*|\s+)([^\s,;]+)"
)


def sensitive_key(value: object) -> bool:
    """Return whether a field/key names credential material."""

    return bool(_SENSITIVE_KEY.search(str(value)))


def redact_text(value: str) -> str:
    """Redact common key/value credential fragments from diagnostic text."""

    return _SENSITIVE_TEXT.sub(lambda match: f"{match.group(1)}={REDACTED}", value)


def primitive(value: Any) -> Any:
    # Redaction comes first: every later branch either recurses or returns the value
    # itself, so a secret reached after this point would be a leak. An unconfigured
    # secret serializes as null rather than a sentinel, so provenance distinguishes
    # "withheld" from "never set".
    if isinstance(value, Secret):
        return REDACTED if value.configured else None
    if isinstance(value, Identifier):
        return value.value
    if isinstance(value, datetime):
        return utc_text(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: REDACTED if sensitive_key(field.name) else primitive(getattr(value, field.name))
            for field in fields(value)
        }
    if isinstance(value, Mapping):
        return {
            str(key): REDACTED if sensitive_key(key) else primitive(item)
            for key, item in value.items()
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [primitive(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        primitive(value), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def canonical_digest(value: Any) -> CanonicalDigest:
    return CanonicalDigest(hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest())

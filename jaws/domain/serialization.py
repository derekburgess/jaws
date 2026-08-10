"""Canonical, secret-free JSON primitives for stable identities."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import fields, is_dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from .identifiers import CanonicalDigest, Identifier
from .secrets import REDACTED, Secret
from .time import utc_text


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
        return {field.name: primitive(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): primitive(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [primitive(item) for item in value]
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(
        primitive(value), ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def canonical_digest(value: Any) -> CanonicalDigest:
    return CanonicalDigest(hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest())

"""Validated, category-separated settings for every JAWS interface.

One object replaces the module-level mix that `jaws.config` used to own. Categories are
separated so a caller depends on the settings it actually uses rather than on a grab bag,
and so run provenance can record the safe ones without hand-picking fields.

Two rules hold everywhere here:

- Construction never requires a credential. Validation of a secret happens when the
  provider is invoked (`require_*`), because the local-embeddings path needs neither
  OpenAI nor IPinfo, and importing a module must never demand either.
- Secrets are `jaws.domain.Secret`, so serializing settings for provenance redacts them
  by construction rather than by a caller remembering to.

Environment variables keep their existing names and precedence; see `docs/dependencies.md`
and the README. Validation raises `SettingsError`, a `ValueError` subclass, so callers
that already catch `ValueError` around configuration keep working.
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, replace
from types import MappingProxyType
from typing import Any, Final, cast

from jaws.domain.enums import ErrorCategory
from jaws.domain.errors import DomainError
from jaws.domain.secrets import Secret
from jaws.domain.serialization import primitive

DEFAULT_NEO4J_URI: Final = "bolt://localhost:7687"
DEFAULT_NEO4J_USERNAME: Final = "neo4j"
DEFAULT_DATABASE: Final = "captures"
DEFAULT_OPENAI_EMBEDDING_MODEL: Final = "text-embedding-3-large"
DEFAULT_PACKET_MODEL: Final = "jina-code"

# Local embedding models, selectable by short id (jaws-compute --model <id>). They run
# fully on-device via sentence-transformers, which reads each model's own pooling and
# normalization config — so adding a model needs no new code, just an id -> HF name entry.
DEFAULT_PACKET_MODELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "jina-code": "jinaai/jina-embeddings-v2-base-code",
        # Cisco's security-domain bi-encoder (ModernBERT, 768-dim, sentence-transformers
        # native). An alternative to jina-code's code-token specialization: tests whether a
        # cybersecurity-trained embedder clusters endpoints better. Both are 768-dim, so the
        # downstream PCA/DBSCAN path is unchanged. Predownload via `jaws-utils --model securebert`.
        "securebert": "cisco-ai/SecureBERT2.0-biencoder",
        # Add more here, e.g.:
        # "bge-small": "BAAI/bge-small-en-v1.5",
        # "nomic": "nomic-ai/nomic-embed-text-v1.5",
        # "gte-base": "thenlper/gte-base",
    }
)

NEO4J_PASSWORD_GUIDANCE: Final = (
    "Export it in the environment, or pass it through your MCP client's env block "
    "(see jaws_mcp/mcp-local.json)."
)


class SettingsError(ValueError):
    """Invalid configuration, carrying a typed `DomainError` for machine-facing callers.

    Subclasses `ValueError` so existing `except ValueError` handlers around configuration
    — notably the MCP error envelope — keep reporting the same class of failure.
    """

    def __init__(self, error: DomainError) -> None:
        super().__init__(error.message)
        self.error = error


def _invalid(code: str, message: str, **details: Any) -> SettingsError:
    return SettingsError(
        DomainError(
            category=ErrorCategory.CONFIGURATION,
            code=code,
            message=message,
            details=details,
        )
    )


def _require_text(code: str, label: str, value: str) -> None:
    if not value.strip():
        raise _invalid(code, f"{label} cannot be empty.")


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Neo4j connection identity. The password is required only to open a driver."""

    uri: str = DEFAULT_NEO4J_URI
    username: str = DEFAULT_NEO4J_USERNAME
    password: Secret = field(default_factory=Secret)
    name: str = DEFAULT_DATABASE

    def __post_init__(self) -> None:
        _require_text("settings.database.uri", "NEO4J_URI", self.uri)
        # Catching this here is the difference between a usable message and the driver's
        # "URI scheme b''", which is what surfaced when credentials never reached the
        # MCP server process.
        if "://" not in self.uri:
            raise _invalid(
                "settings.database.uri_scheme",
                f"NEO4J_URI must include a scheme such as bolt:// or neo4j://, got {self.uri!r}.",
                uri=self.uri,
            )
        _require_text("settings.database.username", "NEO4J_USERNAME", self.username)
        _require_text("settings.database.name", "database name", self.name)

    def require_password(self) -> str:
        try:
            return self.password.require("NEO4J_PASSWORD", NEO4J_PASSWORD_GUIDANCE)
        except ValueError as error:
            raise _invalid("settings.database.password_missing", str(error)) from error


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    """Third-party enrichment and embedding credentials, validated at invocation."""

    ipinfo_api_key: Secret = field(default_factory=Secret)
    openai_api_key: Secret = field(default_factory=Secret)

    def require_ipinfo_api_key(self) -> str:
        try:
            return self.ipinfo_api_key.require("IPINFO_API_KEY")
        except ValueError as error:
            raise _invalid("settings.provider.ipinfo_key_missing", str(error)) from error

    def require_openai_api_key(self) -> str:
        try:
            return self.openai_api_key.require("OPENAI_API_KEY")
        except ValueError as error:
            raise _invalid("settings.provider.openai_key_missing", str(error)) from error


@dataclass(frozen=True, slots=True)
class ModelSettings:
    """Embedding model selection, independent of whichever provider serves it."""

    openai_embedding_model: str = DEFAULT_OPENAI_EMBEDDING_MODEL
    packet_models: Mapping[str, str] = DEFAULT_PACKET_MODELS
    default_packet_model: str = DEFAULT_PACKET_MODEL

    def __post_init__(self) -> None:
        object.__setattr__(self, "packet_models", MappingProxyType(dict(self.packet_models)))
        _require_text(
            "settings.model.openai_embedding_model",
            "OpenAI embedding model",
            self.openai_embedding_model,
        )
        if self.default_packet_model not in self.packet_models:
            raise _invalid(
                "settings.model.unknown_default",
                f"default packet model {self.default_packet_model!r} is not a registered model.",
                known=sorted(self.packet_models),
            )

    def resolve_packet_model(self, model_id: str) -> str:
        try:
            return self.packet_models[model_id]
        except KeyError as error:
            raise _invalid(
                "settings.model.unknown_model",
                f"unknown packet model {model_id!r}.",
                known=sorted(self.packet_models),
            ) from error


@dataclass(frozen=True, slots=True)
class ArtifactStoreSettings:
    """Where rendered and retained artifacts are written."""

    finder_endpoint: str | None = None

    def resolved_finder_endpoint(self) -> str:
        return self.finder_endpoint or os.path.join(tempfile.gettempdir(), "jaws")


@dataclass(frozen=True, slots=True)
class RuntimeSettings:
    """Process-level limits that are not analytical parameters."""

    mcp_timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.mcp_timeout_seconds is not None and self.mcp_timeout_seconds <= 0:
            raise _invalid(
                "settings.runtime.mcp_timeout",
                f"JAWS_MCP_TIMEOUT must be positive, got {self.mcp_timeout_seconds}.",
                mcp_timeout_seconds=self.mcp_timeout_seconds,
            )


@dataclass(frozen=True, slots=True)
class InterfaceSettings:
    """How results are presented to whoever called."""

    agent_mode: bool = False


@dataclass(frozen=True, slots=True)
class Settings:
    """The complete validated configuration for one process."""

    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    provider: ProviderSettings = field(default_factory=ProviderSettings)
    model: ModelSettings = field(default_factory=ModelSettings)
    artifacts: ArtifactStoreSettings = field(default_factory=ArtifactStoreSettings)
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)
    interface: InterfaceSettings = field(default_factory=InterfaceSettings)

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        agent_mode: bool | None = None,
    ) -> Settings:
        """Build settings from a mapping, defaulting to the process environment.

        `env` is injectable so tests never mutate `os.environ`. Falsy values are treated
        as unset: the URI and username fall back to the standard local-install values
        because the process environment is not guaranteed to carry them — MCP clients
        spawn the server as a child process and may strip it (the Python MCP SDK
        whitelists only PATH/HOME and similar), and GUI-launched clients never see shell
        exports at all.
        """
        source = os.environ if env is None else env
        return cls(
            database=DatabaseSettings(
                uri=source.get("NEO4J_URI") or DEFAULT_NEO4J_URI,
                username=source.get("NEO4J_USERNAME") or DEFAULT_NEO4J_USERNAME,
                password=Secret(source.get("NEO4J_PASSWORD")),
            ),
            provider=ProviderSettings(
                ipinfo_api_key=Secret(source.get("IPINFO_API_KEY")),
                openai_api_key=Secret(source.get("OPENAI_API_KEY")),
            ),
            model=ModelSettings(),
            artifacts=ArtifactStoreSettings(
                finder_endpoint=source.get("JAWS_FINDER_ENDPOINT") or None,
            ),
            runtime=RuntimeSettings(
                mcp_timeout_seconds=_optional_positive_int(
                    source.get("JAWS_MCP_TIMEOUT"), "JAWS_MCP_TIMEOUT"
                ),
            ),
            interface=InterfaceSettings(
                agent_mode=_detect_agent_mode() if agent_mode is None else agent_mode,
            ),
        )

    def with_overrides(self, **changes: Any) -> Settings:
        """Return a copy with whole categories replaced, revalidating on the way."""
        unknown = set(changes) - {f.name for f in fields(self)}
        if unknown:
            raise _invalid(
                "settings.unknown_category",
                f"unknown settings categories: {sorted(unknown)}.",
                unknown=sorted(unknown),
            )
        return replace(self, **changes)

    def to_provenance(self) -> dict[str, Any]:
        """Serialize for run provenance. Secrets redact themselves; see `Secret`."""
        return cast(dict[str, Any], primitive(self))


def _detect_agent_mode() -> bool:
    """Raw (non-rich) output when stdout is not a TTY.

    That is exactly the case when a script runs as a subprocess with captured output, as
    the MCP server does. Humans running a script directly in a terminal get the pretty
    rich panels; the MCP server gets clean, parseable text. Detected automatically, so
    callers never have to opt in.
    """
    return not sys.stdout.isatty()


def _optional_positive_int(raw: str | None, label: str) -> int | None:
    if raw is None or not raw.strip():
        return None
    return _parse_int(raw, label)


def _parse_int(raw: str, label: str) -> int:
    try:
        return int(raw)
    except ValueError as error:
        raise _invalid(
            "settings.invalid_integer",
            f"{label} must be an integer, got {raw!r}.",
            value=raw,
        ) from error


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Build settings from the environment without any process-wide caching.

    `jaws.config` holds the one process-wide instance; everything else should either take
    a `Settings` argument or call this, so tests can construct their own.
    """
    return Settings.from_env(env)

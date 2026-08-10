"""Settings contracts: category separation, invocation-time credentials, redaction.

The Milestone 1 completion gate requires that settings serialization proves secrets are
redacted, so the redaction tests here assert on the serialized bytes rather than on a
helper's return value.
"""

import json
from types import SimpleNamespace

import pytest

from jaws import config
from jaws.domain import REDACTED, Secret, canonical_json, primitive
from jaws.settings import (
    DEFAULT_DATABASE,
    DEFAULT_NEO4J_URI,
    DEFAULT_NEO4J_USERNAME,
    DEFAULT_PACKET_MODEL,
    DatabaseSettings,
    ModelSettings,
    ProviderSettings,
    RuntimeSettings,
    Settings,
    SettingsError,
    load_settings,
)

SECRET_VALUE = "correct-horse-battery-staple"

FULL_ENV = {
    "NEO4J_URI": "neo4j://example.internal:7687",
    "NEO4J_USERNAME": "researcher",
    "NEO4J_PASSWORD": SECRET_VALUE,
    "IPINFO_API_KEY": "ipinfo-" + SECRET_VALUE,
    "OPENAI_API_KEY": "openai-" + SECRET_VALUE,
    "JAWS_FINDER_ENDPOINT": "/var/lib/jaws/plots",
    "JAWS_MCP_TIMEOUT": "900",
}


def test_defaults_apply_when_the_environment_is_empty():
    settings = Settings.from_env({}, agent_mode=False)

    assert settings.database.uri == DEFAULT_NEO4J_URI
    assert settings.database.username == DEFAULT_NEO4J_USERNAME
    assert settings.database.name == DEFAULT_DATABASE
    assert settings.database.password.configured is False
    assert settings.model.default_packet_model == DEFAULT_PACKET_MODEL
    assert settings.runtime.mcp_timeout_seconds is None
    assert settings.artifacts.finder_endpoint is None


def test_environment_variables_keep_their_existing_names():
    settings = Settings.from_env(FULL_ENV, agent_mode=False)

    assert settings.database.uri == "neo4j://example.internal:7687"
    assert settings.database.username == "researcher"
    assert settings.artifacts.finder_endpoint == "/var/lib/jaws/plots"
    assert settings.runtime.mcp_timeout_seconds == 900


def test_empty_environment_values_fall_back_rather_than_configuring_blanks():
    """An exported-but-empty variable is the MCP-subprocess case; treat it as unset."""
    settings = Settings.from_env(
        {"NEO4J_URI": "", "NEO4J_USERNAME": "", "NEO4J_PASSWORD": "", "JAWS_MCP_TIMEOUT": ""},
        agent_mode=False,
    )

    assert settings.database.uri == DEFAULT_NEO4J_URI
    assert settings.database.username == DEFAULT_NEO4J_USERNAME
    assert settings.database.password.configured is False
    assert settings.runtime.mcp_timeout_seconds is None


def test_load_settings_does_not_require_any_credential():
    """Importing or constructing must never demand a secret; only invocation may."""
    settings = load_settings({})

    assert settings.provider.openai_api_key.configured is False
    assert settings.provider.ipinfo_api_key.configured is False


@pytest.mark.parametrize(
    "require, expected",
    [
        (lambda s: s.database.require_password(), "NEO4J_PASSWORD"),
        (lambda s: s.provider.require_ipinfo_api_key(), "IPINFO_API_KEY"),
        (lambda s: s.provider.require_openai_api_key(), "OPENAI_API_KEY"),
    ],
)
def test_missing_credentials_fail_only_at_invocation(require, expected):
    settings = Settings.from_env({}, agent_mode=False)

    with pytest.raises(SettingsError) as raised:
        require(settings)

    assert expected in str(raised.value)
    assert raised.value.error.category == "configuration"


def test_settings_error_is_a_value_error_for_existing_handlers():
    """get_neo4j_driver historically raised ValueError; callers still catch that."""
    settings = Settings.from_env({}, agent_mode=False)

    with pytest.raises(ValueError):
        settings.database.require_password()


def test_present_credentials_are_returned_by_require():
    settings = Settings.from_env(FULL_ENV, agent_mode=False)

    assert settings.database.require_password() == SECRET_VALUE
    assert settings.provider.require_openai_api_key() == "openai-" + SECRET_VALUE


def test_openai_client_validates_and_passes_the_typed_secret(monkeypatch):
    settings = Settings.from_env(FULL_ENV, agent_mode=False)
    received = {}

    def openai_client(**kwargs):
        received.update(kwargs)
        return object()

    monkeypatch.setattr(config, "SETTINGS", settings)
    monkeypatch.setattr(
        config,
        "require_module",
        lambda *args: SimpleNamespace(OpenAI=openai_client),
    )
    config.get_openai_client.cache_clear()
    try:
        config.get_openai_client()
    finally:
        config.get_openai_client.cache_clear()

    assert received == {"api_key": "openai-" + SECRET_VALUE}


def test_provider_helpers_validate_only_when_invoked(monkeypatch):
    monkeypatch.setattr(config, "SETTINGS", Settings.from_env({}, agent_mode=False))
    with pytest.raises(SettingsError):
        config.get_ipinfo_api_key()

    monkeypatch.setattr(config, "SETTINGS", Settings.from_env(FULL_ENV, agent_mode=False))
    assert config.get_ipinfo_api_key() == "ipinfo-" + SECRET_VALUE


def test_provenance_redacts_every_configured_secret():
    settings = Settings.from_env(FULL_ENV, agent_mode=False)

    recorded = settings.to_provenance()
    serialized = json.dumps(recorded)

    assert SECRET_VALUE not in serialized
    assert recorded["database"]["password"] == REDACTED
    assert recorded["provider"]["ipinfo_api_key"] == REDACTED
    assert recorded["provider"]["openai_api_key"] == REDACTED


def test_provenance_retains_safe_settings():
    settings = Settings.from_env(FULL_ENV, agent_mode=False)

    recorded = settings.to_provenance()

    assert recorded["database"]["uri"] == "neo4j://example.internal:7687"
    assert recorded["database"]["username"] == "researcher"
    assert recorded["artifacts"]["finder_endpoint"] == "/var/lib/jaws/plots"
    assert recorded["runtime"]["mcp_timeout_seconds"] == 900


def test_unset_secrets_serialize_as_null_not_as_a_redaction_sentinel():
    """Provenance must distinguish "withheld" from "never configured"."""
    recorded = Settings.from_env({}, agent_mode=False).to_provenance()

    assert recorded["database"]["password"] is None
    assert recorded["provider"]["openai_api_key"] is None


def test_canonical_json_of_settings_never_contains_a_secret():
    settings = Settings.from_env(FULL_ENV, agent_mode=False)

    assert SECRET_VALUE not in canonical_json(settings)


def test_secret_does_not_leak_through_repr_str_or_interpolation():
    secret = Secret(SECRET_VALUE)

    assert SECRET_VALUE not in repr(secret)
    assert SECRET_VALUE not in str(secret)
    assert SECRET_VALUE not in f"{secret}"
    assert SECRET_VALUE not in "{}".format(secret)  # noqa: UP032
    assert secret.reveal() == SECRET_VALUE


def test_secret_equality_and_emptiness():
    assert Secret(SECRET_VALUE) == Secret(SECRET_VALUE)
    assert Secret("a") != Secret("b")
    assert Secret(None) == Secret("")
    assert Secret("   ") == Secret(None)
    assert not Secret(None)
    assert Secret(SECRET_VALUE)


def test_secret_survives_nesting_inside_arbitrary_structures():
    """`primitive` must redact wherever a secret appears, not only at the top level."""
    nested = {"outer": [{"inner": Secret(SECRET_VALUE)}]}

    assert SECRET_VALUE not in canonical_json(nested)
    assert primitive(nested)["outer"][0]["inner"] == REDACTED


def test_categories_are_separate_objects():
    settings = Settings.from_env(FULL_ENV, agent_mode=False)

    assert isinstance(settings.database, DatabaseSettings)
    assert isinstance(settings.provider, ProviderSettings)
    assert isinstance(settings.model, ModelSettings)
    assert isinstance(settings.runtime, RuntimeSettings)


def test_categories_can_be_overridden_without_touching_the_rest():
    settings = Settings.from_env(FULL_ENV, agent_mode=False)

    changed = settings.with_overrides(runtime=RuntimeSettings(mcp_timeout_seconds=30))

    assert changed.runtime.mcp_timeout_seconds == 30
    assert changed.database == settings.database
    assert settings.runtime.mcp_timeout_seconds == 900, "the original must be unchanged"


def test_unknown_override_category_is_rejected():
    with pytest.raises(SettingsError):
        Settings.from_env({}, agent_mode=False).with_overrides(nonsense=1)


@pytest.mark.parametrize(
    "env",
    [
        {"NEO4J_URI": "localhost:7687"},
        {"JAWS_MCP_TIMEOUT": "-5"},
        {"JAWS_MCP_TIMEOUT": "soon"},
    ],
)
def test_invalid_values_are_rejected_at_construction(env):
    with pytest.raises(SettingsError):
        Settings.from_env(env, agent_mode=False)


def test_missing_uri_scheme_reports_the_actual_problem():
    with pytest.raises(SettingsError) as raised:
        DatabaseSettings(uri="localhost:7687")

    assert "scheme" in str(raised.value)


def test_unknown_packet_model_is_rejected_with_the_known_set():
    settings = Settings.from_env({}, agent_mode=False)

    with pytest.raises(SettingsError) as raised:
        settings.model.resolve_packet_model("does-not-exist")

    assert raised.value.error.details["known"] == sorted(settings.model.packet_models)


def test_registered_packet_model_resolves_to_its_hub_name():
    settings = Settings.from_env({}, agent_mode=False)

    assert settings.model.resolve_packet_model("jina-code").startswith("jinaai/")


def test_default_packet_model_must_be_registered():
    with pytest.raises(SettingsError):
        ModelSettings(packet_models={"only": "org/only"}, default_packet_model="missing")


def test_packet_models_cannot_be_mutated_through_settings():
    settings = Settings.from_env({}, agent_mode=False)

    with pytest.raises(TypeError):
        settings.model.packet_models["injected"] = "attacker/model"  # type: ignore[index]


def test_settings_are_frozen():
    settings = Settings.from_env({}, agent_mode=False)

    with pytest.raises(AttributeError):
        settings.database.uri = "bolt://elsewhere:7687"  # type: ignore[misc]


def test_agent_mode_is_injectable_rather_than_only_tty_detected():
    assert Settings.from_env({}, agent_mode=True).interface.agent_mode is True
    assert Settings.from_env({}, agent_mode=False).interface.agent_mode is False


def test_artifact_endpoint_falls_back_to_a_temp_directory():
    settings = Settings.from_env({}, agent_mode=False)

    assert settings.artifacts.resolved_finder_endpoint().endswith("jaws")
    assert (
        Settings.from_env(FULL_ENV, agent_mode=False).artifacts.resolved_finder_endpoint()
        == "/var/lib/jaws/plots"
    )

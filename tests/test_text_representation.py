"""Versioned endpoint text-template rendering and model-identity separation."""

from dataclasses import replace

import pytest

from jaws.domain import (
    ENDPOINT_TEXT_TEMPLATE_V1,
    EndpointProfileDraft,
    EntityId,
    TextSequenceFormat,
)
from jaws.jaws_compute import build_endpoint_description, replace_session_profiles
from jaws.services import EndpointTextRenderer, UnsupportedTextTemplateError

EXPECTED_DESCRIPTION = (
    "IP: 8.8.8.8 (public) | Organization: Example Networks | "
    "Hostname: dns.example | Location: Example City\n"
    "Outbound: 100 bytes, 2 packets to 1 peers | Ports: [53, 443]\n"
    "Inbound: 50 bytes, 1 packets from 1 peers | Ports: [53]\n"
    "Protocols: ['TCP', 'UDP']\n"
)


def _draft() -> EndpointProfileDraft:
    return EndpointProfileDraft(
        entity_id=EntityId("ip:8.8.8.8"),
        ip_address="8.8.8.8",
        address_classification="public",
        organization="Example Networks",
        hostname="dns.example",
        location="Example City",
        bytes_out=100,
        packets_out=2,
        out_peers=1,
        out_ports=(443, 53),
        bytes_in=50,
        packets_in=1,
        in_peers=1,
        in_ports=(53,),
        protocols=("UDP", "TCP"),
    )


def _legacy_profile(**overrides):
    values = {
        "ip_address": "8.8.8.8",
        "endpoint_type": "public",
        "org": "Example Networks",
        "hostname": "dns.example",
        "location": "Example City",
        "bytes_out": 100,
        "packets_out": 2,
        "out_peers": 1,
        "out_ports": [53, 443],
        "bytes_in": 50,
        "packets_in": 1,
        "in_peers": 1,
        "in_ports": [53],
        "protocols": ["TCP", "UDP"],
        "interval_mean": None,
        "interval_cv": None,
    }
    values.update(overrides)
    return values


class _RecordingProfileRepository:
    def __init__(self) -> None:
        self.records = ()

    def replace_scope(self, _scope_id, records):
        self.records = records
        return records


def test_endpoint_template_versions_exact_layout_fields_and_format_policies():
    template = ENDPOINT_TEXT_TEMPLATE_V1

    assert template.template_id == "endpoint-description"
    assert template.version == "1"
    assert template.fields == (
        "ip_address",
        "endpoint_type",
        "organization",
        "hostname",
        "location",
        "bytes_out",
        "packets_out",
        "out_peers",
        "out_ports",
        "bytes_in",
        "packets_in",
        "in_peers",
        "in_ports",
        "protocols",
    )
    assert template.missing_value_text == "None"
    assert template.sequence_format is TextSequenceFormat.PYTHON_LIST
    assert template.template.endswith("\n")


def test_pure_renderer_and_legacy_adapter_preserve_exact_embedding_input():
    rendered = EndpointTextRenderer().render(_draft(), ENDPOINT_TEXT_TEMPLATE_V1)
    legacy = build_endpoint_description(
        _legacy_profile(),
        text_template=ENDPOINT_TEXT_TEMPLATE_V1,
    )

    assert rendered == EXPECTED_DESCRIPTION
    assert legacy == EXPECTED_DESCRIPTION


def test_missing_display_values_preserve_declared_legacy_text():
    rendered = EndpointTextRenderer().render(
        replace(_draft(), organization=None, hostname=None, location=None),
        ENDPOINT_TEXT_TEMPLATE_V1,
    )

    assert "Organization: None | Hostname: None | Location: None\n" in rendered


def test_renderer_rejects_unsupported_template_versions_or_content():
    with pytest.raises(UnsupportedTextTemplateError, match="endpoint-description.*'1'"):
        EndpointTextRenderer().render(
            _draft(),
            replace(ENDPOINT_TEXT_TEMPLATE_V1, version="2"),
        )
    with pytest.raises(UnsupportedTextTemplateError, match="endpoint-description.*'1'"):
        EndpointTextRenderer().render(
            _draft(),
            replace(
                ENDPOINT_TEXT_TEMPLATE_V1,
                template=ENDPOINT_TEXT_TEMPLATE_V1.template.replace("IP:", "Address:"),
            ),
        )


def test_template_contract_rejects_placeholder_drift_and_digests_format_policy():
    with pytest.raises(ValueError, match="placeholders must exactly match"):
        replace(
            ENDPOINT_TEXT_TEMPLATE_V1,
            fields=ENDPOINT_TEXT_TEMPLATE_V1.fields[:-1],
        )
    changed = replace(ENDPOINT_TEXT_TEMPLATE_V1, missing_value_text="unknown")

    assert changed.digest != ENDPOINT_TEXT_TEMPLATE_V1.digest


def test_persisted_template_identity_is_independent_of_embedding_model_identity():
    first_repository = _RecordingProfileRepository()
    second_repository = _RecordingProfileRepository()
    profile = _legacy_profile()

    replace_session_profiles(
        (profile,),
        ((0.25, 0.75),),
        "cap_text_template",
        "model-a",
        None,
        "unused",
        first_repository,
        text_template=ENDPOINT_TEXT_TEMPLATE_V1,
    )
    replace_session_profiles(
        (profile,),
        ((0.25, 0.75),),
        "cap_text_template",
        "model-b",
        None,
        "unused",
        second_repository,
        text_template=ENDPOINT_TEXT_TEMPLATE_V1,
    )

    first = first_repository.records[0].identity
    second = second_repository.records[0].identity
    assert (first.representation_id, first.representation_version) == (
        "endpoint-description",
        "1",
    )
    assert (second.representation_id, second.representation_version) == (
        "endpoint-description",
        "1",
    )
    assert first.model_id == "model-a"
    assert second.model_id == "model-b"
    assert first.model_revision == second.model_revision == "runtime-unpinned"

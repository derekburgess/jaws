"""Pure, versioned endpoint representation rendering."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from jaws.domain import ENDPOINT_TEXT_TEMPLATE_V1, TextSequenceFormat, TextTemplateSpec


class UnsupportedTextTemplateError(ValueError):
    """The renderer does not implement the declared text template."""


class EndpointTextEvidence(Protocol):
    """Read-only endpoint fields consumed by the text representation."""

    @property
    def ip_address(self) -> str: ...

    @property
    def address_classification(self) -> str: ...

    @property
    def organization(self) -> str | None: ...

    @property
    def hostname(self) -> str | None: ...

    @property
    def location(self) -> str | None: ...

    @property
    def bytes_out(self) -> int: ...

    @property
    def packets_out(self) -> int: ...

    @property
    def out_peers(self) -> int: ...

    @property
    def out_ports(self) -> tuple[int, ...]: ...

    @property
    def bytes_in(self) -> int: ...

    @property
    def packets_in(self) -> int: ...

    @property
    def in_peers(self) -> int: ...

    @property
    def in_ports(self) -> tuple[int, ...]: ...

    @property
    def protocols(self) -> tuple[str, ...]: ...


@dataclass(frozen=True, slots=True)
class EndpointTextRenderer:
    """Render endpoint evidence with an exact supported template declaration."""

    def validate_template(self, template: TextTemplateSpec) -> None:
        if template != ENDPOINT_TEXT_TEMPLATE_V1:
            raise UnsupportedTextTemplateError(
                "endpoint text renderer supports only template_id='endpoint-description', "
                "version='1'"
            )
        if template.sequence_format is not TextSequenceFormat.PYTHON_LIST:
            raise UnsupportedTextTemplateError("unsupported endpoint sequence format")

    def render(self, profile: EndpointTextEvidence, template: TextTemplateSpec) -> str:
        self.validate_template(template)

        def text(value: object | None) -> str:
            return template.missing_value_text if value is None else str(value)

        values = {
            "ip_address": profile.ip_address,
            "endpoint_type": text(profile.address_classification),
            "organization": text(profile.organization),
            "hostname": text(profile.hostname),
            "location": text(profile.location),
            "bytes_out": profile.bytes_out,
            "packets_out": profile.packets_out,
            "out_peers": profile.out_peers,
            "out_ports": str(list(profile.out_ports)),
            "bytes_in": profile.bytes_in,
            "packets_in": profile.packets_in,
            "in_peers": profile.in_peers,
            "in_ports": str(list(profile.in_ports)),
            "protocols": str(list(profile.protocols)),
        }
        return template.template.format(**values)

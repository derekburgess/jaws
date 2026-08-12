"""Neo4j adapter for bounded endpoint overview and drill-down reads."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import replace
from datetime import datetime
from typing import Any, Protocol, Self, cast

from jaws.domain import (
    EndpointInspection,
    EndpointPacketSample,
    EndpointPeerTraffic,
    EndpointProfile,
    EntityId,
    EntityMetadata,
    utc_text,
)

from .neo4j_profile_repositories import _PROFILE_FIELDS, _profile


class _Record(Protocol):
    def __getitem__(self, key: str) -> Any: ...


class _Result(Protocol):
    def __iter__(self) -> Iterator[_Record]: ...

    def single(self) -> _Record | None: ...


class _Session(Protocol):
    def __enter__(self) -> Self: ...

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None: ...

    def run(self, query: str, parameters: Mapping[str, object] | None = None) -> _Result: ...


class _Driver(Protocol):
    def session(self, *, database: str) -> _Session: ...


_RECENT_PROFILES_QUERY = f"""
MATCH (latest:ENDPOINT)
WHERE latest.SCOPE_ID IS NOT NULL
WITH latest
ORDER BY latest.TIMESTAMP DESC, latest.SCOPE_ID DESC, latest.PROFILE_KEY DESC
LIMIT 1
WITH latest.SCOPE_ID AS latest_scope
MATCH (endpoint:ENDPOINT {{SCOPE_ID: latest_scope}})
WHERE endpoint.TIMESTAMP > datetime($computed_after)
OPTIONAL MATCH (address:IP_ADDRESS {{IP_ADDRESS: endpoint.IP_ADDRESS}})
OPTIONAL MATCH (organization:ORGANIZATION)-[:OWNERSHIP]->(address)
WITH endpoint, address, min(organization.ORGANIZATION) AS fallback_organization
RETURN {_PROFILE_FIELDS},
       fallback_organization,
       address.HOSTNAME AS fallback_hostname,
       address.LOCATION AS fallback_location
ORDER BY endpoint.BYTES_OUT + endpoint.BYTES_IN DESC, endpoint.IP_ADDRESS
LIMIT $limit
"""

_INSPECT_PROFILE_QUERY = f"""
MATCH (endpoint:ENDPOINT {{IP_ADDRESS: $ip_address}})
WITH endpoint
ORDER BY endpoint.TIMESTAMP DESC, endpoint.SCOPE_ID DESC, endpoint.PROFILE_KEY DESC
LIMIT 1
OPTIONAL MATCH (address:IP_ADDRESS {{IP_ADDRESS: endpoint.IP_ADDRESS}})
OPTIONAL MATCH (organization:ORGANIZATION)-[:OWNERSHIP]->(address)
WITH endpoint, address, min(organization.ORGANIZATION) AS fallback_organization
RETURN {_PROFILE_FIELDS},
       fallback_organization,
       address.HOSTNAME AS fallback_hostname,
       address.LOCATION AS fallback_location
"""

_INSPECT_HISTORY_QUERY = f"""
MATCH (endpoint:ENDPOINT {{IP_ADDRESS: $ip_address}})
WHERE endpoint.SCOPE_ID IS NOT NULL
  AND endpoint.SCOPE_ID <> 'scope_pooled_all'
  AND endpoint.CAPTURE_ID IS NOT NULL
  AND endpoint.CAPTURE_ID <> 'all'
OPTIONAL MATCH (scope:OBSERVATION_SCOPE {{SCOPE_ID: endpoint.SCOPE_ID}})-[:INCLUDES]->
               (capture:CAPTURE)
RETURN {_PROFILE_FIELDS},
       toString(coalesce(capture.STARTED_AT, capture.STARTED,
                         endpoint.TIMESTAMP)) AS evidence_started
ORDER BY evidence_started DESC, endpoint.CAPTURE_ID DESC
LIMIT $history_limit
"""

_INSPECT_TOTALS_QUERY = """
MATCH (packet:PACKET)
WHERE packet.SRC_IP = $ip_address OR packet.DST_IP = $ip_address
RETURN count(packet) AS packets,
       count(DISTINCT CASE
           WHEN packet.SRC_IP = $ip_address THEN packet.DST_IP
           ELSE packet.SRC_IP
       END) AS peers
"""

_INSPECT_PEERS_QUERY = """
MATCH (packet:PACKET)
WHERE packet.SRC_IP = $ip_address OR packet.DST_IP = $ip_address
WITH packet,
     CASE
         WHEN packet.SRC_IP = $ip_address THEN packet.DST_IP
         ELSE packet.SRC_IP
     END AS peer,
     packet.SRC_IP = $ip_address AS outbound
WITH peer,
     sum(CASE WHEN outbound THEN packet.SIZE ELSE 0 END) AS bytes_out,
     sum(CASE WHEN outbound THEN 1 ELSE 0 END) AS packets_out,
     sum(CASE WHEN NOT outbound THEN packet.SIZE ELSE 0 END) AS bytes_in,
     sum(CASE WHEN NOT outbound THEN 1 ELSE 0 END) AS packets_in,
     collect(DISTINCT packet.PROTOCOL) AS protocols,
     collect(DISTINCT CASE
         WHEN packet.SRC_PORT > 0 AND packet.DST_PORT > 0
         THEN CASE
             WHEN packet.SRC_PORT < packet.DST_PORT THEN packet.SRC_PORT
             ELSE packet.DST_PORT
         END
     END) AS service_ports,
     collect(DISTINCT CASE
         WHEN packet.SRC_PORT > 0 AND packet.DST_PORT > 0
         THEN CASE
             WHEN packet.SRC_PORT < packet.DST_PORT THEN packet.DST_PORT
             ELSE packet.SRC_PORT
         END
     END) AS high_ports
OPTIONAL MATCH (peer_address:IP_ADDRESS {IP_ADDRESS: peer})
OPTIONAL MATCH (peer_organization:ORGANIZATION)-[:OWNERSHIP]->(peer_address)
WITH peer, peer_address, bytes_out, packets_out, bytes_in, packets_in,
     protocols, service_ports, high_ports,
     min(peer_organization.ORGANIZATION) AS peer_organization
RETURN peer AS peer_ip,
       peer_organization,
       peer_address.HOSTNAME AS peer_hostname,
       peer_address.LOCATION AS peer_location,
       bytes_out, packets_out, bytes_in, packets_in,
       protocols, service_ports, high_ports
ORDER BY bytes_out + bytes_in DESC, peer
LIMIT $peer_limit
"""

_INSPECT_PACKETS_QUERY = """
MATCH (packet:PACKET)
WHERE packet.SRC_IP = $ip_address OR packet.DST_IP = $ip_address
RETURN packet.SRC_IP AS source_ip,
       packet.SRC_PORT AS source_port,
       packet.DST_IP AS destination_ip,
       packet.DST_PORT AS destination_port,
       packet.PROTOCOL AS protocol,
       packet.SIZE AS size_bytes,
       toString(packet.TIMESTAMP) AS observed_at
ORDER BY packet.TIMESTAMP DESC, elementId(packet)
LIMIT $packet_limit
"""


def _bounded_limit(value: int, name: str) -> int:
    if value < 0:
        raise ValueError(f"{name} cannot be negative")
    return value


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"stored inspection record is missing {name}")
    return value


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _datetime(value: object, name: str) -> datetime:
    text = _required_text(value, name)
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _optional_port(value: object) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("stored inspection packet port must be an integer or null")
    return value


def _profile_with_fallback(row: _Record) -> EndpointProfile:
    profile = _profile(row)
    return replace(
        profile,
        organization=profile.organization or _optional_text(row["fallback_organization"]),
        hostname=profile.hostname or _optional_text(row["fallback_hostname"]),
        location=profile.location or _optional_text(row["fallback_location"]),
    )


def _peer(row: _Record) -> EndpointPeerTraffic:
    address = _required_text(row["peer_ip"], "peer IP address")
    service_ports = {int(value) for value in (row["service_ports"] or ()) if value}
    high_ports = {int(value) for value in (row["high_ports"] or ()) if value}
    return EndpointPeerTraffic(
        peer=EntityMetadata(
            entity_id=EntityId(f"ip:{address}"),
            ip_address=address,
            organization=_optional_text(row["peer_organization"]),
            hostname=_optional_text(row["peer_hostname"]),
            location=_optional_text(row["peer_location"]),
        ),
        bytes_out=int(row["bytes_out"] or 0),
        packets_out=int(row["packets_out"] or 0),
        bytes_in=int(row["bytes_in"] or 0),
        packets_in=int(row["packets_in"] or 0),
        protocols=tuple(str(value) for value in (row["protocols"] or ()) if value),
        service_ports=tuple(service_ports),
        ephemeral_ports=len(high_ports - service_ports),
    )


def _packet(row: _Record) -> EndpointPacketSample:
    return EndpointPacketSample(
        source_ip=_required_text(row["source_ip"], "source IP address"),
        source_port=_optional_port(row["source_port"]),
        destination_ip=_required_text(row["destination_ip"], "destination IP address"),
        destination_port=_optional_port(row["destination_port"]),
        protocol=_required_text(row["protocol"], "protocol"),
        size_bytes=int(row["size_bytes"] or 0),
        observed_at=_datetime(row["observed_at"], "packet timestamp"),
    )


class Neo4jInspectionRepository:
    """Optimized read-only graph projections for MCP inspection tools."""

    def __init__(self, driver: object, database: str) -> None:
        if not database.strip():
            raise ValueError("database name cannot be empty")
        self._driver = cast(_Driver, driver)
        self.database = database

    def recent_profiles(
        self, *, computed_after: datetime, limit: int
    ) -> tuple[EndpointProfile, ...]:
        parameters: dict[str, object] = {
            "computed_after": utc_text(computed_after),
            "limit": _bounded_limit(limit, "profile limit"),
        }
        with self._driver.session(database=self.database) as session:
            return tuple(
                _profile_with_fallback(row)
                for row in session.run(_RECENT_PROFILES_QUERY, parameters)
            )

    def inspect(
        self,
        entity_id: EntityId,
        *,
        peer_limit: int,
        packet_limit: int,
        history_limit: int,
    ) -> EndpointInspection:
        address = entity_id.value.removeprefix("ip:")
        parameters: dict[str, object] = {
            "ip_address": address,
            "peer_limit": _bounded_limit(peer_limit, "peer limit"),
            "packet_limit": _bounded_limit(packet_limit, "packet limit"),
            "history_limit": _bounded_limit(history_limit, "history limit"),
        }
        with self._driver.session(database=self.database) as session:
            profile_row = session.run(_INSPECT_PROFILE_QUERY, parameters).single()
            totals = session.run(_INSPECT_TOTALS_QUERY, parameters).single()
            peers = tuple(_peer(row) for row in session.run(_INSPECT_PEERS_QUERY, parameters))
            packets = tuple(_packet(row) for row in session.run(_INSPECT_PACKETS_QUERY, parameters))
            history = tuple(
                _profile(row) for row in session.run(_INSPECT_HISTORY_QUERY, parameters)
            )
        return EndpointInspection(
            entity_id=entity_id,
            profile=_profile_with_fallback(profile_row) if profile_row is not None else None,
            total_packets=int(totals["packets"] or 0) if totals is not None else 0,
            total_peers=int(totals["peers"] or 0) if totals is not None else 0,
            history=history,
            peers=peers,
            packets=packets,
        )

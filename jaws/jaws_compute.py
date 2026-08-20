import argparse
from dataclasses import dataclass
from datetime import UTC, datetime

import pandas as pd
from rich.console import Group

from jaws.adapters import SystemClock
from jaws.config import (
    CONSOLE,
    DATABASE,
    DEFAULT_PACKET_MODEL,
    OPENAI_EMBEDDING_MODEL,
    PACKET_MODELS,
    get_openai_client,
)
from jaws.domain import (
    ENDPOINT_NUMERIC_FEATURE_SET_V1,
    MIN_TIMING_PACKETS,
    CaptureId,
    EndpointProfile,
    EndpointProfileDraft,
    EntityDefinition,
    EntityId,
    EntityMetadata,
    EntityType,
    ObservationScopeId,
    ObservationWindow,
    ProfileIdentity,
    ProfileStatus,
    RetentionPolicy,
    normalized_ip,
)
from jaws.jaws_utils import (
    Reporter,
    dbms_connection,
    render_activity_panel,
    render_info_panel,
)
from jaws.optional_dependencies import require_module
from jaws.services import EndpointProfiler, RetentionService, interval_timing_seconds
from jaws.storage import Neo4jProfileRepository, Neo4jRepositories


def fetch_packets(driver, database, capture_id=None, repository=None):
    # PACKET nodes carry the 5-tuple + size as properties, so per-IP aggregation
    # reads straight off them (one scan) — no traversal needed. `capture_id` scopes
    # the scan to one session; None means every packet in the graph (--session all,
    # or a legacy graph with no CAPTURE nodes).
    repository = repository or Neo4jRepositories.connect(driver, database).packets
    records = (
        repository.read(ObservationWindow(capture_ids=(CaptureId(capture_id),)))
        if capture_id is not None
        else repository.read_all()
    )
    return pd.DataFrame(
        [
            {
                "src_ip": record.source_ip,
                "dst_ip": record.destination_ip,
                "src_port": record.source_port,
                "dst_port": record.destination_port,
                "size": record.size_bytes,
                "protocol": record.protocol,
                "ts_ms": int(record.observed_at.timestamp() * 1000),
                "capture_id": record.capture_id.value,
            }
            for record in records
        ]
    )


def resolve_session(driver, database, session_arg, repository=None):
    """Turn --session (latest | all | <capture id>) into a concrete packet scope.

    Returns (capture_id, session_ids): `capture_id` is the concrete session to filter
    packets on, or None for no filter ('all', or a legacy graph with no CAPTURE
    nodes); `session_ids` is every session in the graph, oldest first, so callers can
    report what was available. Raises ValueError for an explicit id that doesn't exist.
    """
    repository = repository or Neo4jRepositories.connect(driver, database).captures
    captures = sorted(
        repository.list_all(),
        key=lambda record: (
            record.started_at or record.registered_at,
            record.capture_id.value,
        ),
    )
    session_ids = [record.capture_id.value for record in captures]
    if session_arg == "all":
        return None, session_ids
    if session_arg == "latest":
        # A legacy graph (captured before sessions existed) has packets but no
        # CAPTURE nodes — treat the whole graph as one implicit session.
        return (session_ids[-1] if session_ids else None), session_ids
    if session_arg not in session_ids:
        raise ValueError(
            f"session '{session_arg}' not found; available: {session_ids or 'none (no CAPTURE nodes — use latest or all)'}"
        )
    return session_arg, session_ids


def fetch_ip_metadata(driver, database, repository=None):
    # Org/hostname/location per IP, set by jaws_ipinfo (org name on the org node,
    # hostname/location on the IP node).
    repository = repository or Neo4jRepositories.connect(driver, database).enrichment
    return {
        record.ip_address: {
            "ip_address": record.ip_address,
            "org": record.organization,
            "hostname": record.hostname,
            "location": record.location,
        }
        for record in repository.list_metadata()
    }


def endpoint_timing(ts_groups):
    """Inter-packet cadence for one packet stream (one endpoint, one direction).

    `ts_groups` is a list of timestamp arrays (ms), ONE PER CAPTURE SESSION: intervals
    are computed within each group and pooled, never across groups, so the dead gap
    between two capture runs doesn't register as one giant interval (which would
    inflate a real beacon's CV — the opposite failure of the tiny-burst false beacon).
    Returns (interval_mean, interval_cv) in seconds, or (None, None) when the pooled
    intervals are fewer than MIN_TIMING_PACKETS - 1 (the single-stream equivalent of
    the packet gate). `interval_cv` (std/mean of inter-packet gaps) is the beaconing
    signal: a low CV means highly regular callbacks (C2-like), a high CV means
    bursty/human traffic. `interval_mean` is the typical gap, i.e. the period.
    """
    return interval_timing_seconds(
        (
            (float(value) / 1000.0 for value in timestamps if not pd.isna(value))
            for timestamps in ts_groups
        ),
        minimum_packets=MIN_TIMING_PACKETS,
    )


_MISSING_PROTOCOL = "jaws-legacy-missing-protocol"
_LEGACY_UNSCOPED_CAPTURE_ID = CaptureId("legacy-unscoped")
_ENDPOINT_ENTITY_DEFINITION = EntityDefinition(
    entity_type=EntityType.ENDPOINT_IP,
    version="1",
)


@dataclass(frozen=True, slots=True)
class _LegacyProfilePacket:
    """Outer compatibility projection; not valid modern packet evidence."""

    capture_id: CaptureId
    observed_at: datetime
    protocol: str
    size_bytes: int
    source_ip: str
    destination_ip: str
    source_port: int | None
    destination_port: int | None


def _optional_frame_port(value):
    return None if pd.isna(value) else int(value)


def _frame_profile_packets(packets):
    records = []
    for _, row in packets.iterrows():
        raw_capture_id = row.get("capture_id")
        capture_id = (
            _LEGACY_UNSCOPED_CAPTURE_ID.value
            if raw_capture_id is None or pd.isna(raw_capture_id) or not str(raw_capture_id).strip()
            else str(raw_capture_id)
        )
        raw_timestamp = row.get("ts_ms")
        timestamp_seconds = (
            0.0
            if raw_timestamp is None or pd.isna(raw_timestamp)
            else float(raw_timestamp) / 1000.0
        )
        raw_protocol = row.get("protocol")
        protocol = (
            _MISSING_PROTOCOL
            if raw_protocol is None or pd.isna(raw_protocol) or not str(raw_protocol).strip()
            else str(raw_protocol)
        )
        records.append(
            _LegacyProfilePacket(
                capture_id=CaptureId(capture_id),
                observed_at=datetime.fromtimestamp(timestamp_seconds, UTC),
                protocol=protocol,
                size_bytes=int(row["size"]),
                source_ip=str(row["src_ip"]),
                destination_ip=str(row["dst_ip"]),
                source_port=_optional_frame_port(row.get("src_port")),
                destination_port=_optional_frame_port(row.get("dst_port")),
            )
        )
    return tuple(records)


def _metadata_records(metadata):
    records = []
    for key, values in metadata.items():
        address = normalized_ip(str(values.get("ip_address") or key))
        records.append(
            EntityMetadata(
                entity_id=EntityId(f"ip:{address}"),
                ip_address=address,
                organization=values.get("org"),
                hostname=values.get("hostname"),
                location=values.get("location"),
            )
        )
    return tuple(records)


def _legacy_profile(draft: EndpointProfileDraft):
    return {
        "ip_address": draft.ip_address,
        "endpoint_type": draft.address_classification,
        "org": draft.organization,
        "hostname": draft.hostname,
        "location": draft.location,
        "bytes_out": draft.bytes_out,
        "packets_out": draft.packets_out,
        "out_peers": draft.out_peers,
        "out_ports": list(draft.out_ports),
        "bytes_in": draft.bytes_in,
        "packets_in": draft.packets_in,
        "in_peers": draft.in_peers,
        "in_ports": list(draft.in_ports),
        "protocols": [protocol for protocol in draft.protocols if protocol != _MISSING_PROTOCOL],
        "interval_mean": draft.interval_mean,
        "interval_cv": draft.interval_cv,
    }


def profile_observation_window(capture_id, session_ids, packets):
    """Translate legacy session selection into an explicit evidence declaration."""

    captures: tuple[CaptureId, ...]
    if capture_id is not None:
        captures = (CaptureId(capture_id),)
    elif session_ids:
        captures = tuple(CaptureId(value) for value in session_ids)
    else:
        values = (
            ()
            if "capture_id" not in packets.columns
            else tuple(
                sorted(
                    {
                        str(value).strip()
                        for value in packets["capture_id"]
                        if not pd.isna(value) and str(value).strip()
                    }
                )
            )
        )
        captures = (
            tuple(CaptureId(value) for value in values)
            if values
            else (_LEGACY_UNSCOPED_CAPTURE_ID,)
        )
    return ObservationWindow(capture_ids=captures)


def build_endpoint_profiles(
    packets,
    metadata,
    *,
    entity_definition=None,
    observation_window=None,
    numeric_feature_set=None,
):
    """Aggregate every packet into one profile per IP address, split by direction.

    Each IP becomes a single data point describing its outbound traffic (as the
    source) and inbound traffic (as the destination), so outbound anomalies are
    first-class. The local host is included intentionally.
    """
    evidence = _frame_profile_packets(packets)
    window = observation_window or ObservationWindow(
        capture_ids=tuple(
            sorted(
                {packet.capture_id for packet in evidence} or {_LEGACY_UNSCOPED_CAPTURE_ID},
                key=lambda value: value.value,
            )
        )
    )
    result = EndpointProfiler(allow_legacy_negative_packet_sizes=True).profile(
        evidence,
        entity_definition=entity_definition or _ENDPOINT_ENTITY_DEFINITION,
        observation_window=window,
        numeric_feature_set=numeric_feature_set or ENDPOINT_NUMERIC_FEATURE_SET_V1,
        metadata=_metadata_records(metadata),
    )
    return [_legacy_profile(draft) for draft in result.profiles]


def build_endpoint_description(p):
    return (
        f"IP: {p['ip_address']} ({p['endpoint_type']}) | Organization: {p['org']} | Hostname: {p['hostname']} | Location: {p['location']}\n"
        f"Outbound: {p['bytes_out']} bytes, {p['packets_out']} packets to {p['out_peers']} peers | Ports: {p['out_ports']}\n"
        f"Inbound: {p['bytes_in']} bytes, {p['packets_in']} packets from {p['in_peers']} peers | Ports: {p['in_ports']}\n"
        f"Protocols: {p['protocols']}\n"
    )


def profile_scope_id(session_scope):
    if session_scope == "all":
        return ObservationScopeId("scope_pooled_all")
    return ObservationScopeId(f"scope_{session_scope}")


def replace_session_profiles(
    profiles,
    embeddings,
    session_scope,
    model_name,
    driver,
    database,
    repository=None,
):
    """Atomically replace one complete, explicitly versioned profile set."""

    scope_id = profile_scope_id(session_scope)
    computed_at = SystemClock().now()
    records = tuple(
        EndpointProfile(
            identity=ProfileIdentity(
                entity_id=EntityId(f"ip:{profile['ip_address']}"),
                scope_id=scope_id,
                representation_id="endpoint-description",
                representation_version="legacy-v1",
                model_id=model_name,
                # The legacy CLI accepts mutable provider/model names but no immutable
                # revision. Record that limitation explicitly rather than inventing one.
                model_revision="runtime-unpinned",
            ),
            legacy_scope=session_scope,
            computed_at=computed_at,
            address_classification=profile["endpoint_type"],
            organization=profile["org"],
            hostname=profile["hostname"],
            location=profile["location"],
            bytes_out=profile["bytes_out"],
            packets_out=profile["packets_out"],
            out_peers=profile["out_peers"],
            out_ports=tuple(profile["out_ports"]),
            bytes_in=profile["bytes_in"],
            packets_in=profile["packets_in"],
            in_peers=profile["in_peers"],
            in_ports=tuple(profile["in_ports"]),
            protocols=tuple(profile["protocols"]),
            interval_mean=profile.get("interval_mean"),
            interval_cv=profile.get("interval_cv"),
            embedding=tuple(embedding),
        )
        for profile, embedding in zip(profiles, embeddings, strict=True)
    )
    repository = repository or Neo4jProfileRepository(driver, database)
    return repository.replace_scope(scope_id, records)


# Profiles are cheap per row but each carries an embedding vector, so an unbounded
# accumulation of profile sets grows the graph without bound. Retention keeps the N
# most recently computed scopes (by compute TIMESTAMP) — far more history than the
# baseline needs — and drops the rest. PACKET/CAPTURE history is never touched: an
# older session can always be re-profiled with --session <capture_id>.
def prune_profile_sessions(driver, database, retain, repository=None):
    if retain is None or retain <= 0:
        return 0, []
    repository = repository or Neo4jProfileRepository(driver, database)
    from jaws.adapters import SystemClock, UuidAuditEventIdGenerator
    from jaws.domain import AuditContext

    service = RetentionService(
        repository,
        SystemClock(),
        UuidAuditEventIdGenerator(),
        AuditContext("jaws_compute", "jaws-compute", database),
    )
    result = service.apply(service.plan(RetentionPolicy.legacy_profile_limit(retain)))
    return result.deleted_profile_records, [
        summary.legacy_scope for summary in result.plan.deleted_profile_scopes
    ]


def count_profile_sessions(driver, database, repository=None):
    repository = repository or Neo4jProfileRepository(driver, database)
    return sum(
        summary.status is not ProfileStatus.LEGACY_QUARANTINED
        for summary in repository.list_scopes()
    )


def _local_embedding_runtime():
    """Load the local model stack only for ``--api transformers``."""

    torch = require_module("torch", "local-embeddings", "Local embeddings")
    sentence_transformers = require_module(
        "sentence_transformers", "local-embeddings", "Local embeddings"
    )
    device = "cuda" if torch.cuda.is_available() else "cpu"
    return torch, sentence_transformers.SentenceTransformer, device


def compute_transformer_embeddings(descriptions, embedder):
    # sentence-transformers reads each model's own pooling config and applies it; with
    # normalize_embeddings it L2-normalizes for calibrated cosine geometry downstream.
    # One function works for any model in PACKET_MODELS — no per-model code. Encoding
    # the whole profile set in one call lets the library batch on the device, which is
    # dramatically faster than per-profile encodes on a GPU.
    return embedder.encode(descriptions, normalize_embeddings=True).tolist()


# One request per chunk instead of one per profile — fewer round-trips and less
# rate-limit exposure. 512 short profiles stays well inside the API's per-request
# input-count and token limits.
OPENAI_EMBEDDING_BATCH = 512


# Default number of computed profile sets kept in the graph (see prune_profile_sessions).
# The baseline needs only a handful of prior sessions; 20 leaves generous headroom while
# bounding the stored embedding vectors.
PROFILE_RETENTION_SESSIONS = 20


def compute_openai_embeddings(client, descriptions):
    embeddings = []
    for start in range(0, len(descriptions), OPENAI_EMBEDDING_BATCH):
        chunk = descriptions[start : start + OPENAI_EMBEDDING_BATCH]
        response = client.embeddings.create(input=chunk, model=OPENAI_EMBEDDING_MODEL)
        # The API tags each embedding with its input index; sort to guarantee the
        # output order matches the input order.
        embeddings.extend(item.embedding for item in sorted(response.data, key=lambda d: d.index))
    return embeddings


def main():
    parser = argparse.ArgumentParser(
        description="Compute per-IP endpoint embeddings using either OpenAI or Transformers."
    )
    parser.add_argument(
        "--api",
        choices=["openai", "transformers"],
        default="openai",
        help="Specify the API to use for computing embeddings, either 'openai' or 'transformers' (default: 'openai' — for easy demos without a GPU; note the MCP server defaults to 'transformers', the preferred path on a GPU host).",
    )
    parser.add_argument(
        "--model",
        choices=list(PACKET_MODELS),
        default=DEFAULT_PACKET_MODEL,
        help=f"Local transformers model to use when --api transformers (default: '{DEFAULT_PACKET_MODEL}'). Add more in config.PACKET_MODELS.",
    )
    parser.add_argument(
        "--database",
        default=DATABASE,
        help=f"Specify the database to connect to (default: '{DATABASE}').",
    )
    parser.add_argument(
        "--session",
        default="latest",
        help="Which capture session to profile: 'latest' (default), 'all' (every packet in the graph; timing still never crosses session boundaries), or a specific CAPTURE_ID from a capture run.",
    )
    parser.add_argument(
        "--retain-profiles",
        type=int,
        default=PROFILE_RETENTION_SESSIONS,
        help=f"How many computed profile sets (sessions) to keep in the graph; older ones are pruned after this run (default: {PROFILE_RETENTION_SESSIONS}). Profiles accumulate per session so jaws-finder can baseline an endpoint against its own history. 0 disables pruning. Raw PACKET/CAPTURE history is never pruned.",
    )
    args = parser.parse_args()
    reporter = Reporter()
    torch_runtime = None
    sentence_transformer = None
    device = None
    if args.api == "transformers":
        try:
            torch_runtime, sentence_transformer, device = _local_embedding_runtime()
        except ModuleNotFoundError as e:
            reporter.error("ERROR", str(e))
            return
    driver = dbms_connection(args.database, reporter)
    if driver is None:
        return

    try:
        repositories = Neo4jRepositories.connect(driver, args.database)
    except Exception as e:
        reporter.error("ERROR", str(e))
        driver.close()
        return

    try:
        capture_id, session_ids = resolve_session(
            driver, args.database, args.session, repositories.captures
        )
    except ValueError as e:
        reporter.error("ERROR", str(e))
        driver.close()
        return
    # The scope stamped on each ENDPOINT and reported back: a concrete session id, or
    # 'all' when unscoped ('all' requested, or a legacy graph with no CAPTURE nodes).
    session_scope = capture_id if capture_id else "all"
    reporter.info(
        "CONFIG", f"Profiling session: {session_scope} ({len(session_ids)} session(s) in graph)"
    )

    packets = fetch_packets(driver, args.database, capture_id, repositories.packets)
    metadata = fetch_ip_metadata(driver, args.database, repositories.enrichment)
    profiles = build_endpoint_profiles(
        packets,
        metadata,
        entity_definition=_ENDPOINT_ENTITY_DEFINITION,
        observation_window=profile_observation_window(capture_id, session_ids, packets),
        numeric_feature_set=ENDPOINT_NUMERIC_FEATURE_SET_V1,
    )

    model_name = PACKET_MODELS[args.model] if args.api == "transformers" else OPENAI_EMBEDDING_MODEL
    embedding_strings = []
    embedding_tensors = []
    embedder = None

    processing_message = f"Embedding {len(profiles)} endpoint profiles using: {model_name}{f' ({device})' if args.api == 'transformers' else ''}"

    def render():
        return Group(
            render_info_panel("CONFIG", processing_message, CONSOLE),
            render_activity_panel("EMBEDDINGS(STR)", embedding_strings, CONSOLE),
            render_activity_panel(
                "EMBEDDINGS(TENSOR)", [str(tensor) for tensor in embedding_tensors], CONSOLE
            ),
        )

    try:
        if args.api == "transformers":
            embedder = sentence_transformer(model_name, device=device, trust_remote_code=True)

        # Embed every profile in one batched pass (the panels then narrate the DB
        # writes). reporter.info first so a human sees progress during a long encode.
        reporter.info("CONFIG", processing_message)
        descriptions = [build_endpoint_description(profile) for profile in profiles]
        if not descriptions:
            embeddings = []
        elif args.api == "transformers":
            embeddings = compute_transformer_embeddings(descriptions, embedder)
        else:
            embeddings = compute_openai_embeddings(get_openai_client(), descriptions)

        with reporter.activity(render) as update:
            for profile, description, embedding in zip(profiles, descriptions, embeddings):
                embedding_strings.append(description)
                embedding_tensors.append(embedding)
                update()

        replace_session_profiles(
            profiles,
            embeddings,
            session_scope,
            model_name,
            driver,
            args.database,
            repositories.profiles,
        )

        # Compatibility adapter: the explicit retention service runs after the write so
        # this run's own set is always among the kept. Operators can inspect the same
        # policy independently through `jaws-retention dry-run`.
        pruned, pruned_scopes = prune_profile_sessions(
            driver, args.database, args.retain_profiles, repositories.profiles
        )
        if pruned:
            reporter.info(
                "CONFIG",
                f"Pruned {pruned} profile(s) from {len(pruned_scopes)} session(s) beyond the {args.retain_profiles} most recent: {', '.join(pruned_scopes)}",
            )

        # Profile sets now in the graph — how much per-endpoint history jaws-finder can
        # baseline against (1 means this run only: no history yet, baseline is a no-op).
        profiled_scopes = count_profile_sessions(driver, args.database, repositories.profiles)
        reporter.result(
            {
                "database": args.database,
                "api": args.api,
                "model": model_name,
                "session": session_scope,
                "sessions_in_graph": len(session_ids),
                "endpoints_embedded": len(embedding_strings),
                "packets": len(packets),
                "profiled_sessions": profiled_scopes,
                "profiles_pruned": pruned,
            },
            summary=f"Embedded {len(embedding_strings)} endpoint profiles (one per IP) from {len(packets)} packets (session: {session_scope}) via {args.api} in: '{args.database}'",
        )
        return

    except Exception as e:
        reporter.error("ERROR", str(e))

    finally:
        if embedder is not None:
            del embedder
        if torch_runtime is not None and torch_runtime.cuda.is_available():
            torch_runtime.cuda.empty_cache()
        driver.close()


if __name__ == "__main__":
    main()

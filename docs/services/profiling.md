# Endpoint profiling service contract

`EndpointProfiler` is the deterministic packet-to-entity aggregation boundary. It requires
an `EntityDefinition`, an `ObservationWindow`, a `NumericFeatureSet`, immutable
`PacketRecord` evidence, and optional `EntityMetadata` display projections. It returns an
`EndpointProfilingResult` containing those exact declarations, the source packet count,
and address-sorted `EndpointProfileDraft` values. It imports no pandas, NumPy, Neo4j,
embedding provider, configuration, CLI, or reporting dependency.

Drafts are deliberately pre-embedding. Endpoint-IP drafts bind normalized IP identity,
deterministic address classification, directional packet features, display metadata, and
cadence. Host-destination drafts bind the declared capture-host perspective to one remote
peer and retain host-relative upload/download evidence. Observation scope, representation
version, model revision, computation time, embedding, and persistence are later service
responsibilities and are not invented by aggregation.

## Entity and observation declarations

The current profiler accepts only `endpoint_ip` entity semantics at version `1`.
Host-destination, flow, service, subnet, or unknown entity versions fail with
`UnsupportedEntityDefinitionError`; they are never silently treated as endpoint IPs.

Every packet capture ID must be declared by the observation window. Optional start and end
bounds are inclusive, and evidence outside either bound fails with `ProfilingWindowError`.
A declared capture may legitimately contribute zero packets, so an empty packet sequence
returns a successful empty result rather than erasing the window declaration. Multiple
capture IDs form an explicit pooled input while cadence continues to respect each packet's
capture boundary. Perspective and filter declarations remain attached to the result;
evidence-selection adapters own evaluation of their external filter syntax.

Observation windows reject duplicate capture IDs and empty filter declarations. Entity
definition versions are normalized and cannot be blank.

## Numeric feature declaration

The current numeric representation is `endpoint_profile_numeric` version `1`. Feature
order is contractual because every matrix column, historical median, reason code, and
unit label depends on it. `jaws-finder` derives those legacy projections from this
declaration rather than maintaining independent constants.

| Order | Feature | Family | Unit | Raw transformation | Missing values |
| ---: | --- | --- | --- | --- | --- |
| 1 | `bytes_out` | base | bytes | identity | forbidden |
| 2 | `bytes_in` | base | bytes | identity | forbidden |
| 3 | `packets_out` | base | packets | identity | forbidden |
| 4 | `packets_in` | base | packets | identity | forbidden |
| 5 | `out_peers` | base | peers | identity | forbidden |
| 6 | `in_peers` | base | peers | identity | forbidden |
| 7 | `bytes_out_in_ratio` | shape | ratio | `bytes_out / (bytes_in + 1)` | forbidden |
| 8 | `packets_out_in_ratio` | shape | ratio | `packets_out / (packets_in + 1)` | forbidden |
| 9 | `bytes_per_packet` | shape | bytes/packet | `(bytes_out + bytes_in) / (packets_out + packets_in + 1)` | forbidden |
| 10 | `bytes_per_peer` | shape | bytes/peer | `bytes_out / (out_peers + 1)` | forbidden |
| 11 | `interval_mean` | timing | seconds | identity | population median, or zero if all missing |
| 12 | `interval_cv` | timing | ratio | identity | population median, or zero if all missing |

All twelve raw columns declare `log1p` as their analysis transformation before robust
scoring or numeric clustering. The `+1` safe-ratio offsets and timing imputation are
therefore versioned behavior, not implementation accidents. Changing a source field,
order, unit, transformation, offset, or missing-value policy changes the feature-set
digest and requires a new feature-set version.

The pure profiler currently accepts only this exact feature set and rejects other IDs,
versions, or definitions with `UnsupportedNumericFeatureSetError`. The legacy compute
adapter supplies version 1 for existing callers, so current CLI and Benchmark 0 output
remain unchanged.

## Endpoint text representation

`ENDPOINT_TEXT_TEMPLATE_V1` declares `endpoint-description` version `1` independently
from any embedding provider or model. The declaration contains the exact four-line
template, ordered placeholders, missing-display-value text (`None`), Python-list sequence
formatting, and terminal newline. All of those choices participate in its canonical
digest; changing any of them requires a new template version.

`EndpointTextRenderer` accepts typed endpoint evidence and this exact declaration. It
renders IP/classification and display metadata, outbound counts/peers/ports, inbound
counts/peers/ports, and protocols without importing pandas, an embedding stack, or a
provider client. Unsupported IDs, versions, or template content fail with
`UnsupportedTextTemplateError` rather than being stamped as version 1.

The legacy `build_endpoint_description` function is now only a dictionary adapter into
that renderer. Its text remains byte-for-byte compatible, including list punctuation and
the final newline. The compute CLI passes the same template declaration to rendering and
profile persistence. Stored `representation_id`/`representation_version` therefore name
the template (`endpoint-description`/`1`), while `model_id` and `model_revision` remain
separate fields. The current CLI still records `runtime-unpinned` for model revision; exact
provider/model provenance belongs to the later embedding-provider checkpoint.

## Directional aggregation invariants

Every packet contributes independently to its source's outbound view and destination's
inbound view:

| Feature | Outbound meaning | Inbound meaning |
| --- | --- | --- |
| Bytes and packets | Evidence sent by the endpoint | Evidence received by the endpoint |
| Peers | Distinct destinations | Distinct sources |
| Ports | Destination services contacted | Destination ports receiving traffic |
| Protocols | Union across sent and received evidence | Union across sent and received evidence |

Ports are unique, numerically sorted, and limited to the first 20 for compatibility.
Protocols and final profiles are sorted deterministically. The legacy `0.0.0.0` non-IP
placeholder is excluded when it appears on either side of a packet. The local capture host
is otherwise retained as an ordinary profile entity.

Provider or legacy display metadata may populate organization, hostname, and location, but
it cannot change counts, identity, classification, or cadence. Researcher annotations and
ground truth remain separate evidence.

## Timing behavior

Timing is calculated independently for sent and received streams. Timestamps are sorted
inside each capture session, adjacent intervals are calculated only within that session,
and the intervals are then pooled. Gaps between capture sessions never become traffic
intervals.

A direction needs at least `MIN_TIMING_PACKETS` (currently six) worth of pooled intervals:
five or more intervals. Sparse timing remains `(None, None)`. Qualifying timing reports the
mean interval in seconds and population coefficient of variation. Nonpositive means remain
undefined. When both directions qualify, the direction with the lower coefficient of
variation supplies the endpoint cadence; its mean is retained with it. This preserves the
existing beacon signal instead of interleaving request/response gaps.

These semantics are machine-readable in `NumericFeatureSet.timing_evidence`, alongside the
ordered timing features themselves. Endpoint numeric version 1 declares outbound and
inbound as separate eligible directions, lower coefficient of variation as the selection
rule, six packets as the per-direction minimum, and within-capture interval boundaries.
Changing any of those values changes the feature-set digest. `EndpointProfiler` also rejects
a runtime packet gate that disagrees with the declared contract.

Each qualifying `EndpointProfileDraft` records the selected `timing_direction`; drafts with
insufficient evidence record no direction and no timing values. This provenance belongs to
the typed profiling result. The frozen legacy dictionary projection intentionally omits it
so existing CLI and Benchmark 0 consumers retain their exact input shape.

## Determinism and entity definitions

`ProfileService` supports endpoint-IP version 1 with `endpoint_profile_numeric` version 1
and host-destination version 1 with `host_destination_numeric` version 1. The older
`EndpointProfiler` name remains an alias for callers migrating to the entity-neutral API.
Unsupported entity/feature combinations fail before aggregation.

Endpoint and host-destination drafts are sorted by stable identity. Packet ordering,
metadata ordering, sets of peers/ports/protocols, and within-capture timestamp ordering do
not affect the result. Duplicate display metadata for one address is rejected rather than
resolved by input order. `ProfilingResult.digest` is the canonical digest of the entity
definition, observation window, numeric feature declaration, source count, and ordered
drafts, so identical evidence and declarations produce the same retained identity.

Host-destination profiling requires `ObservationWindow.perspective` to name an IP entity.
Only peers the host sent traffic to become profiles, matching the legacy host-outbound
surface; reverse packets contribute download evidence, unrelated traffic is ignored, and
the scoped entity identity includes both host perspective and destination IP.

## Legacy compatibility

`jaws_compute.build_endpoint_profiles` remains the pandas-facing compatibility function
used by the current CLI and Benchmark 0 harness. It converts the legacy DataFrame and
metadata dictionary into typed records, calls `EndpointProfiler`, and projects drafts back
to the unchanged dictionary/list shape. A narrowly named compatibility switch permits the
frozen synthetic Benchmark 0 rows to retain their historical signed individual sizes;
normal profiler calls still reject negative sizes, and those rows are never promoted into
modern `PacketRecord` evidence. Packet aggregation no longer lives in the command module.

`jaws-compute` translates a concrete `--session` into a one-capture window, `--session all`
into the complete declared capture set, and a legacy graph with no capture records into the
explicit `legacy-unscoped` compatibility identity. Direct legacy callers may omit the new
arguments only at this outer adapter; the pure service has no implicit entity or scope.

The next Milestone 3 slice extracts typed local and remote embedding providers behind the
common representation service boundary.

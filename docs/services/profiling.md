# Endpoint profiling service contract

`EndpointProfiler` is the deterministic packet-to-entity aggregation boundary. It requires
an `EntityDefinition`, an `ObservationWindow`, immutable `PacketRecord` evidence, and
optional `EntityMetadata` display projections. It returns an `EndpointProfilingResult`
containing those exact declarations, the source packet count, and address-sorted
`EndpointProfileDraft` values. It imports no pandas, NumPy, Neo4j, embedding provider,
configuration, CLI, or reporting dependency.

The draft is deliberately pre-representation and pre-embedding. It binds normalized IP
identity, deterministic address classification, directional packet features, display
metadata, and cadence. Observation scope, entity-definition version, representation
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

The next profile-service slice versions numeric feature definitions, transformations,
missing-value policy, and units.

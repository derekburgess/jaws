# Capture and packet repositories

Milestone 2 stores capture metadata and packet evidence behind inward-facing repository
contracts. Application and interface code depends on `jaws.ports`; Neo4j-specific Cypher
and record translation live in `jaws.storage`.

## CaptureRepository

`CaptureRepository` owns capture registration, canonical lookup, non-unique legacy-alias
lookup, chronological catalog listing, and lifecycle transitions. A transition supplies
the state the caller observed and fails on a conflict. Canonical identity and provenance
fields cannot change during a transition; only lifecycle timestamps, state, stored packet
count, and failure code advance.

Registration rejects duplicate canonical IDs. A legacy timestamp alias is intentionally
not unique because simultaneous captures must remain independently addressable.

## PacketRepository

`PacketRepository` accepts a bounded sequence whose records all name the requested active
capture. A non-empty batch is one transaction: it is either fully retained or contributes
no packets. Writes to missing or terminal captures fail. An empty append is a no-op.

Reads require an `ObservationWindow`, preserve its declared capture order, sort packets by
original observation time within each capture, and apply inclusive optional start/end
bounds. The repository does not infer an imported capture's host perspective.

`PacketRecord` normalizes IPv4/IPv6 text, uses aware UTC datetimes, expresses missing
transport ports as `None`, and retains the existing payload text when present. The Neo4j
adapter maps missing ports back to the legacy numeric `0` property without creating a
`PORT` node.

## Implementations and schema guard

The deterministic in-memory repositories and Neo4j repositories run through the same
behavioral contract. `Neo4jRepositories.connect` validates the managed schema before it
exposes adapters; callers must migrate through `jaws-schema` or `initialize_schema` first.
The Neo4j implementation dual-writes version-2 and legacy capture properties described in
the [schema contract](neo4j-schema.md).

Repository errors distinguish duplicates, missing captures, optimistic state conflicts,
inactive captures, and unsupported schema state without exposing Neo4j exception types to
application code.

# Enrichment service contract

`EnrichmentService` is the deterministic boundary between provider adapters and the
versioned enrichment repository. It has no IPinfo, Neo4j, settings, credential,
presentation, or optional-dependency import. Tests, CLI, MCP, benchmarks, and future
agents can invoke the same bounded behavior with a deterministic provider and clock.

## Provider-neutral records

`EnrichmentObservation` represents one provider response before entity ownership and
acquisition time are bound. It carries provider ID/revision, outcome, optional ASN,
organization, hostname, location, coordinates, confidence, and a non-secret failure code.
Resolved metadata is legal only for `succeeded`; success requires at least one meaningful
metadata value. A provider cannot manufacture a shared `Unknown` organization to turn an
empty response into apparent evidence.

The service binds an observation to the normalized IP entity and its injected acquisition
clock as an `EnrichmentRecord`, then persists it through `EnrichmentRepository`.
Researcher-authored annotations and ground-truth flags remain separate records and are
never promoted into provider claims.

## Classification and caching

Address classification uses only Python's pinned standard-library `ipaddress` behavior.
The ordered result is one of `multicast`, `broadcast`, `unspecified`, `loopback`,
`link-local`, `private`, `public`, `reserved`, or `unknown`. Only `public` addresses reach
the remote provider. Every other pending address receives a `not_applicable` record from
the versioned `jaws-address-classifier`, with the classification retained as its reason.

The repository is the cache. `succeeded`, `not_applicable`, `not_found`, and
`permanent_failure` are terminal for later pending-inventory reads. Only
`transient_failure` is eligible for another attempt. Every attempt has an acquisition
timestamp and exact provider revision, so a retry replaces the prior current observation
without losing the meaning of its outcome in exported evidence created beforehand.

Legacy non-public addresses attached to the old synthetic `Unknown` organization are
detached before pending inventory is read. Public legacy ownership remains compatibility
metadata rather than being relabeled as modern provider evidence.

## IPinfo adapter

`IpinfoEnrichmentProvider` lazily creates one handler for the run, so a database containing
only non-public or already-terminal addresses requires neither the optional package nor an
API key. It maps meaningful response fields without inventing fallbacks:

| Provider outcome | Enrichment status |
| --- | --- |
| At least one meaningful metadata field | `succeeded` |
| Empty response or HTTP 404 | `not_found` |
| HTTP 408, 425, 429, 5xx, or connection/OS failure | `transient_failure` |
| Other provider error | `permanent_failure` |

Configuration and missing-dependency failures occur before a provider request and remain
interface errors; they are not persisted as false observations about an IP address.

## CLI compatibility and remaining follow-up

`jaws-ipinfo` retains its `--database` flag and structured result fields:
`addresses_scanned`, `addresses_skipped_non_public`,
`addresses_already_documented`, and `organizations_added`. It now owns only connection,
provider construction, progress rendering, and compatibility serialization.

The next enrichment slice must add an explicit bounded rate-limit and in-run retry/backoff
policy with an injected wait strategy and deterministic tests. Current transient outcomes
are safely retryable on a later invocation, but the service deliberately does not sleep or
retry within the same run yet.

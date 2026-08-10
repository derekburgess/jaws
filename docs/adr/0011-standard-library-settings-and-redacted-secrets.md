# ADR-0011: Validate settings with the standard library and redact secrets by construction

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-05 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0006](0006-deterministic-core-and-adapter-boundaries.md), [ADR-0008](0008-capability-extras-and-direct-constraints.md), [ADR-0009](0009-standard-library-domain-contracts.md), [`IMPLEMENTATION_PLAN.md`](../../IMPLEMENTATION_PLAN.md#milestone-1--project-foundation-and-typed-contracts) |

## Context

`jaws/config.py` mixed several concerns in one module-level namespace: Neo4j connection
identity, provider credentials, embedding model registries, the plot output path, output
mode derived from TTY detection, hosting-ASN reference data, and lazily constructed
clients. Every consumer imported the flat names, so a module needing a database name also
imported credentials, and there was no object to hand to run provenance.

Two constraints shaped the replacement. Credentials must not be required to import or
construct anything: the local-embeddings path needs neither OpenAI nor IPinfo, and MCP
clients spawn the server as a child process that may carry no environment at all. And the
Milestone 1 completion gate requires that settings serialization proves secrets are
redacted, which is a property of the serializer rather than of each caller remembering.

This record chooses standard-library validation for process settings only. It does not
choose a schema or validation library for untrusted external experiment specifications.

## Decision

Settings use frozen, slotted standard-library dataclasses in `jaws/settings.py`, split
into database, provider, model, artifact-store, runtime, and interface
categories under one `Settings` root. `Settings.from_env` takes an injectable mapping so
tests never mutate `os.environ`. Validation raises `SettingsError`, a `ValueError`
subclass carrying a typed `DomainError` in the `configuration` category, so machine-facing
callers get a stable code while existing `except ValueError` handlers keep working.

Credentials are `jaws.domain.Secret`, a deliberately non-dataclass value object whose
`__repr__` and `__str__` both redact. `jaws.domain.serialization.primitive` redacts it as
its first branch, before any recursion or fall-through, so a secret nested anywhere in a
structure cannot reach canonical JSON. An unconfigured secret serializes as null rather
than as a sentinel, so provenance distinguishes "withheld" from "never set". Reading a
value is always an explicit `reveal` or `require` call, and `require` is what turns a
missing credential into an actionable message at invocation time.

`jaws/config.py` remains as a thin compatibility layer that owns the one process-wide
`SETTINGS` and derives the legacy flat names from it, so existing installations, CLI
modules, and the MCP server are unchanged.

## Alternatives considered

Pydantic would provide coercion, generated schemas, and `SecretStr`. It arrives
transitively through the MCP SDK but is not a declared direct dependency, so adopting it
here would put a validation framework in the deterministic core that ADR-0006 and ADR-0009
deliberately keep free of one, and would make settings behavior depend on that framework's
major version. The settings surface is roughly ten environment variables with defaults,
which does not justify that boundary. Keeping module-level constants and adding ad-hoc
redaction at each logging site was rejected because it makes leakage the default and
correctness a matter of vigilance at every call site.

Storing secrets as plain strings inside the settings dataclasses was rejected because
`primitive` walks dataclass fields, so provenance would have serialized them.

## Consequences

Categories can be replaced independently via `with_overrides`, and a run can record
`to_provenance()` without curating a safe subset. Reading a credential is more verbose
than attribute access, which is the intended cost. `Secret` must stay a non-dataclass, and
any future canonical serializer must keep redaction ahead of its dataclass branch; both are
covered by tests. The legacy flat names in `jaws/config.py` are duplication that persists
until the Milestone 3 CLI adapters take settings directly.

**No new configuration is introduced.** This change reorganizes the settings that already
existed and adds nothing an operator must learn. `JAWS_MCP_TIMEOUT` moves from an ad-hoc
read in `jaws_mcp/server.py` into runtime settings, keeping its existing name, default, and
semantics; every other variable keeps its name and precedence exactly.

The dividing line, which this ADR treats as binding: the environment carries connection
details and credentials, and everything a run does stays on the command. Anything already
expressed by a CLI flag — `--interface`, `--duration`, `--database`, `--model`, `--session`
— must not gain an environment equivalent, because a hidden global that changes what a
command does without appearing in the command is the opposite of what a reproducible run
needs.

There is consequently **no capture settings category**. The plan lists "capture" among the
categories to separate, but no capture configuration exists today: interface and duration
are per-run arguments, not settings. Capture acquisition parameters become a first-class
object at Milestone 3 as `CaptureSpec`, which is where they belong. Recording an empty
category honestly is better than inventing contents to fill it.

## Benchmark impact

None. This is a configuration-plumbing change with no analytical surface. The synthetic
benchmark smoke report reproduces every Benchmark 0 rank, score, and reason list exactly,
and the `recall` tier retains its three known quality failures unchanged.

## Migration

Milestone 1 introduces the settings object behind the existing flat names. Milestone 3
rewires the CLI adapters and the enrichment service to take settings directly and moves
`HOSTING_ASNS` into that service, after which the compatibility aliases are removed.

## Reversal conditions

Reconsider if external specification validation — JSON Schema generation, coercion of
untrusted experiment inputs — outgrows the standard library. That is a decision about
external specs, which may adopt a framework at the adapter boundary without moving one
into settings or the domain core. A replacement must preserve invocation-time credential
validation, redaction by construction, injectable environments, and frozen categories.

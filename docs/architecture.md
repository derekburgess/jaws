# Package dependency directions

JAWS dependencies point inward. The initial enforceable layers are deliberately small;
later milestones add services and adapters to this same graph rather than bypassing it.

```text
interfaces / adapters / infrastructure
                 |
          application services
                 |
               ports
                 |
              domain
```

## Enforced boundaries

| Package | May import from JAWS | Must not import |
| --- | --- | --- |
| `jaws.domain` | `jaws.domain` only | Ports, services, adapters, legacy commands, infrastructure, interfaces |
| `jaws.ports` | `jaws.domain`, `jaws.ports` | Services, adapters, legacy commands, concrete providers/storage, interfaces |
| `jaws.services` | `jaws.domain`, `jaws.ports`, `jaws.services` | Adapters, legacy commands, concrete providers/storage, interfaces |
| `jaws.settings` | `jaws.domain` | Ports, services, adapters, concrete providers/storage, interfaces |

All four layers may import the Python standard library. They may not import third-party
packages. The architecture test parses imports rather than importing modules, so an
optional dependency cannot hide an invalid direction merely because it is unavailable in
the test environment.

The protocols are intentionally provider- and storage-neutral. Neo4j, PyShark, IPinfo,
OpenAI, sentence-transformers, CLI, and MCP code implement or consume these contracts from
outer layers; none belongs in `jaws.domain` or `jaws.ports`. The deterministic fakes live
beside the contracts because service unit tests need the same lightweight install boundary.

`jaws.services` contains deterministic use-case coordination over inward ports. Ingest
assigns capture identity, binds source packet observations to that session, writes bounded
batches, and finalizes complete, partial, failed, or cancelled state without owning packet
capture privileges. Retention planning consumes typed policies and profile summaries,
produces a mutation-free dry-run plan, and applies only an unchanged exact plan. Evidence
transfer consumes typed snapshots, builds checksummed bundles, and imports only into an
unchanged compatible empty target. The service layer does not import Neo4j, configuration,
CLI, filesystem, packet-capture providers, or reporting code.

`jaws.storage` is an outer infrastructure package. It depends inward on domain and port
contracts; its repository adapters accept driver-compatible objects without importing the
optional Neo4j package, so migration and repository records remain importable in a core
installation. All executable Cypher belongs here. Capture, enrichment, compute, finder,
utility, schema, evidence, administration, and MCP interfaces delegate graph operations to
storage adapters. A global architecture ratchet scans every runtime Python module outside
`jaws.storage` for structural Cypher markers, so future interfaces and services inherit the
same boundary without being named individually.

`jaws.adapters` contains outer implementations of inward ports. Its standard runtime clock
and UUID capture-ID generator import only domain contracts and the standard library.
PyShark packet-source adapters own live-capture privileges, callback/iterator bridging,
decoded-packet policy, and capture-handle cleanup without importing PyShark until the CLI
loads that optional capability. Later provider/interface adapters may carry their declared
optional dependencies without moving those dependencies into domain or ports.

## Ratchet policy

The import-direction test governs every Python file under a listed inner package. A new
inner package must be added to the layer map in the same change. Separately, the Cypher-
ownership test governs every runtime Python file under `jaws` and `jaws_mcp`, excluding only
the owning `jaws.storage` package. Removing a governed package, adding a storage exception,
or weakening an allowed edge requires an ADR or an explicit implementation-plan entry.

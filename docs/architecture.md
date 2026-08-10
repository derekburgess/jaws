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
| `jaws.settings` | `jaws.domain` | Ports, services, adapters, concrete providers/storage, interfaces |

All three layers may import the Python standard library. They may not import third-party
packages. The architecture test parses imports rather than importing modules, so an
optional dependency cannot hide an invalid direction merely because it is unavailable in
the test environment.

The protocols are intentionally provider- and storage-neutral. Neo4j, PyShark, IPinfo,
OpenAI, sentence-transformers, CLI, and MCP code implement or consume these contracts from
outer layers; none belongs in `jaws.domain` or `jaws.ports`. The deterministic fakes live
beside the contracts because service unit tests need the same lightweight install boundary.

## Ratchet policy

The test governs every Python file under a listed package. A new inner package must be
added to the layer map in the same change. Existing legacy command modules are not declared
compliant prematurely; each later service extraction adds its new package to this test
before moving behavior behind it. Removing a governed package or weakening an allowed edge
requires an ADR or an explicit implementation-plan entry.

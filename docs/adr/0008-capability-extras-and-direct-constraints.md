# ADR-0008: Use capability extras and reviewed direct constraints

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-03 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [ADR-0006](0006-deterministic-core-and-adapter-boundaries.md), [Benchmark 0 environment](../../benchmarks/baseline-0/environment.json), [dependency profiles](../dependencies.md), [`IMPLEMENTATION_PLAN.md`](../../IMPLEMENTATION_PLAN.md#milestone-1--project-foundation-and-typed-contracts) |

## Context

JAWS previously declared every runtime package in one unversioned list. A development
install therefore pulled database, capture, provider, plotting, MCP, and local-model
stacks even when a test used only numerical profiles and ranking. Benchmark 0 confirmed
that an unqualified Linux Torch installation also resolved CUDA and NVIDIA packages,
turning a correctness checkout into a multi-gigabyte accelerator installation.

The project needs repeatable direct dependency choices without claiming that one exact
transitive graph can represent Linux, macOS, Windows, capture tooling, and CPU/GPU Torch
wheels equally. It must also preserve the complete legacy installation while making the
smallest useful analytical surface genuinely lightweight.

## Decision

JAWS will use five lightweight base dependencies and capability-specific optional extras
for Neo4j, capture, enrichment, OpenAI embeddings, local embeddings, plotting, MCP, and
the future agent laboratory. A compatibility `all` extra contains every current runtime
capability. The `dev` extra contains correctness and artifact-contract tooling but does
not install provider, database, MCP, plotting, or local-model stacks.

Imports across those boundaries are lazy. Importing or executing the numeric benchmark
must not import Neo4j, OpenAI, Torch, sentence-transformers, PyShark, IPinfo, Matplotlib,
Plotille, or MCP. Selecting OpenAI embeddings must not install or import the local-model
stack. Invoking an unavailable capability raises an installation message naming its
extra.

`pyproject.toml` owns compatible direct-dependency ranges. A reviewed Python 3.12
constraints file pins every direct dependency exactly. Install profiles apply that file;
benchmark and experiment environments retain the complete resolved transitive inventory.
Platform-specific full locks may be added with the container/runtime work, but they do
not replace package ranges or the portable run-environment record.

Python 3.12 remains the sole supported baseline until another version passes the entire
profile, compatibility, and benchmark matrix and the support policy is updated explicitly.

## Alternatives considered

### Keep one complete mandatory dependency set

This is simple to explain and matches the legacy installation, but forces CUDA/model,
database, capture, plotting, and interface packages into numerical and OpenAI-only work.
It also prevents import-boundary tests from detecting accidental coupling.

### Make every third-party package optional

This minimizes the base package further, but NumPy, pandas, scikit-learn, Kneed, and Rich
are used across the current numerical/CLI surface. Splitting them now would create many
tiny profiles without a useful executable core.

### Fully lock one universal transitive dependency graph

This can reproduce one machine closely, but Torch accelerators, operating-system wheels,
and capture dependencies make the result misleading or unusable elsewhere. Complete
platform locks remain appropriate for later named container/runtime profiles.

### Publish separate JAWS distributions for each capability

Separate packages provide strong isolation but would add release coordination and API
versioning before the typed internal boundaries exist. Extras provide the needed install
boundary without committing to a multi-distribution architecture.

## Consequences

### Benefits

- Numeric and OpenAI-only installations avoid the local model and CUDA stack.
- A lightweight development checkout can run deterministic correctness tests.
- Each new dependency has an explicit capability owner and reviewed version.
- Full run inventories preserve scientific provenance despite platform-specific
  transitive resolution.
- The legacy complete runtime remains available through `all` and `requirements.txt`.

### Costs and limitations

- Users must select multiple extras for a complete workflow.
- Optional-capability failures occur at invocation rather than package import time.
- Direct constraints do not alone reproduce every transitive wheel byte-for-byte.
- The `all` and local-model profiles remain large and may resolve platform-specific
  accelerator packages.

### Follow-on constraints

- Core and development groups may not acquire provider, database, plotting, capture,
  interface, agent, or local-model dependencies without a superseding decision.
- New dependencies must be assigned to one capability and added to the reviewed direct
  constraint set in the same change.
- CI and container installs must apply the constraints file or a later profile-specific
  lock derived under this policy.
- Run artifacts continue to record the complete installed environment.

## Benchmark impact

No scoring, feature, threshold, label, explanation, or ordering change is intended.
Benchmark 0 rankings and the seven CLI compatibility cases must remain exact. The new
numeric profile test executes all eight synthetic scenarios with optional integration
imports blocked, proving that parity does not rely on the former monolithic environment.

## Migration

Move mandatory numerical packages into `project.dependencies`, declare capability
extras, and retain `requirements.txt` as the constrained complete-runtime wrapper. Add a
lightweight `requirements-dev.txt`, lazy-load integrations at invocation, and verify each
profile in a disposable virtual environment. Existing `pip install .` users now receive
the numerical core; full legacy users migrate to `pip install -r requirements.txt` or
select the documented extras.

## Reversal conditions

Revisit the boundary if measured install complexity outweighs the isolation benefit, a
dependency cannot be loaded safely at invocation, or stable typed packages justify
separate distributions. A replacement must retain lightweight numeric/OpenAI profiles,
explicit dependency ownership, benchmark parity, and complete environment provenance.

## References

- [Dependency and installation profiles](../dependencies.md)
- [Benchmark 0 environment](../../benchmarks/baseline-0/environment.json)
- [Deterministic core boundary](0006-deterministic-core-and-adapter-boundaries.md)
- [Official MCP Python SDK v2 migration guide](https://py.sdk.modelcontextprotocol.io/v2/migration/)

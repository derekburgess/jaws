# Research workflow

JAWS turns a question about network behavior into a falsifiable, retained study. The
workflow is **Orient → Hypothesize → Experiment → Observe (OHEO)**. Each step produces an
inspectable object; neither an agent nor an analyst narrative can replace the deterministic
experiment and evaluation records.

## 1. Orient

Begin with the governed catalog rather than an arbitrary local path. List available
datasets, captures, versioned components, earlier experiments, and retained findings:

```console
jaws-research components --catalog examples/research/catalog.json --json
```

MCP clients use `research_orient` for the complete catalog-and-prior-run view. The CLI's
`components` command provides the governed catalog snapshot; `jaws-research operation` is
reserved for one declared experiment variant and analytical stage.

The catalog is an authority boundary. It declares stable IDs and bounded evidence; the
experiment specification refers to those IDs. Raw host paths, credentials, Cypher, shell
commands, and packet contents are not experiment parameters.

## 2. Hypothesize

A useful hypothesis names a treatment, a control, each metric's `maximize` or `minimize`
objective, and an acceptable regression. A regression budget is the maximum permitted
movement opposite that declared objective. For example:

> Adding host-relative upload/download asymmetry improves exfiltration Recall@3 over the
> `legacy_2_0` control without increasing benign burden@3 by more than one observation.

The complete example in [`examples/research/control-treatment.json`](../examples/research/control-treatment.json)
binds that statement to an observation window, entity definition, representation,
reference, ranker, evaluator, renderer, seeds, and regression budget. Validate it before
execution:

```console
jaws-research validate examples/research/control-treatment.json \
  --catalog examples/research/catalog.json
```

Canonical specification content determines the experiment identity. Runtime details such
as the code revision, package inventory, timestamps, hardware, and container digest belong
to each append-only run record.

## 3. Experiment

Run the declared control/treatment study through the same service used by benchmarks and
MCP:

```console
jaws-research run examples/research/control-treatment.json \
  --catalog examples/research/catalog.json \
  --root .jaws-research --json
```

Long MCP work uses `experiment_start`, followed by `experiment_status` and
`experiment_result`; cancellation is cooperative through `experiment_cancel`. A run never
overwrites an earlier run. Repeating the same specification creates a new run ID so
determinism and stochastic stability can be measured rather than hidden.

## 4. Observe

Verify bundles before interpreting them, then compare the retained control and treatment:

```console
CONTROL_BUNDLE=.jaws-research/bundles/experiments/EXPERIMENT_ID/runs/CONTROL_RUN_ID
TREATMENT_BUNDLE=.jaws-research/bundles/experiments/EXPERIMENT_ID/runs/TREATMENT_RUN_ID
jaws-research verify "$CONTROL_BUNDLE" --json
jaws-research inspect "$CONTROL_BUNDLE" --json
jaws-research compare "$CONTROL_BUNDLE" "$TREATMENT_BUNDLE" --json
```

The JSON returned by `run` provides the exact `bundle` paths; callers do not reconstruct
them from display labels. Raw metric deltas remain `treatment - control`, while support,
refutation, and regression budgets follow each hypothesis metric's declared objective.

Read the reward as a vector. Recall@k describes target coverage; reciprocal rank describes
how quickly the target appears; benign burden counts non-target attention; stability shows
whether ranks survive seeds/windows; runtime, memory, and provider cost expose operational
tradeoffs. A new ranker is not better merely because one scalar rose.

Every ranked finding carries one or more `EvidencePointer` records. Pass a returned pointer
to MCP's `inspect_evidence` tool rather than reconstructing one from a display label. That
operation returns bounded catalog metadata or artifact evidence; it does not grant a client
an arbitrary graph query or raw filesystem read. `jaws-research inspect` inspects a retained
bundle and its checksums; it is not a generic evidence query.

## Benchmark interpretation

Benchmark 0 is the immutable observational freeze of the pre-refactor detector. The
`legacy_2_0` ranker is the named compatibility control. Benchmark v1 runs every registered
ranker over declared datasets, scenarios, seeds, and windows, preserving individual run
bundles plus JSON/Markdown/HTML views of one report object.

The full release profile excludes held-out labels unless the operator explicitly follows
the held-out governance procedure. Metadata-only external datasets remain unavailable, not
successful. Synthetic results establish controlled behavior and regression evidence; they
do not establish field threat-detection quality.

## Optional agent laboratory

`jaws-lab` exercises OHEO through a bounded `ResearchGateway`. It may propose hypotheses
and experiments and summarize deterministic observations. It cannot calculate rewards,
alter labels, access held-out ground truth, run shell/Cypher, capture packets, or perform
destructive administration. Exact approval is required before experiment execution. See
[`agent-laboratory.md`](agent-laboratory.md) for the trace and isolation contract.

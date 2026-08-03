# ADR-0001: Define JAWS as a network anomaly-ranking research workbench

| Field | Value |
| --- | --- |
| Status | Accepted |
| Date | 2026-08-03 |
| Decision owners | Project maintainers |
| Supersedes | None |
| Superseded by | None |
| Related | [`README.md`](../../README.md), [`IMPLEMENTATION_PLAN.md`](../../IMPLEMENTATION_PLAN.md) |

## Context

JAWS began as a packet-capture and endpoint-anomaly toolkit. Its strongest
capabilities are not autonomous threat classification or product workflow: they
are behavioral representations, peer and historical comparison, interpretable
rankings, and evidence drill-down. Describing the project primarily as an
AI-enabled detector or as an MCP packet-analysis pipeline obscures those strengths
and encourages claims the existing evidence cannot support.

The intended users are security researchers, blue teams, and technically capable
white/gray-hat researchers working in environments they are authorized to study.
They need to challenge methods, inspect failures, reproduce results, and trace a
ranked item back to network evidence. They do not need JAWS to hide uncertainty
behind a polished threat verdict.

## Decision

JAWS is an open research workbench for investigating which representations,
comparisons, and ranking methods surface behaviorally meaningful anomalies in
network traffic, so researchers can form hypotheses, run reproducible
experiments, and trace ranked findings back to packet evidence.

The project's primary research question is:

> Which representations of network traffic, evaluated against which reference
> populations and ranked by which methods, most reliably bring meaningful
> anomalies to an investigator's attention?

JAWS allocates investigative attention by producing ranked, explained findings.
It does not convert anomaly scores into autonomous malicious/benign verdicts.
Packet evidence remains authoritative, and the experiment is the primary unit of
reproducibility.

The project is explicitly not optimized as:

- a consumer application or turnkey commercial SOC product;
- a production IDS/IPS or an autonomous response system;
- a source of ground truth derived from anomaly scores or LLM interpretations;
- proof of detector quality based only on synthetic scenarios; or
- a system that equates an IP address permanently with one device or person.

An anomaly may be malicious, benign, novel, misconfigured, or simply worth
understanding. Interfaces and reports must preserve that distinction.

## Alternatives considered

### Product-oriented intrusion detector

This framing offers a simpler sales and user story, but it implies calibrated
threat verdicts, operational reliability, false-positive guarantees, and response
workflows that JAWS has not established and is not presently intended to pursue.

### Packet-analysis pipeline exposed through MCP

This accurately describes part of the current implementation and is easy to
demonstrate. It makes an interface mechanism the project's identity, however, and
does not explain why the underlying ranking research is distinctive.

### Autonomous agent for network investigation

An agent may improve research ergonomics, but centering it would couple the
project's mission to one orchestration approach and risk treating persuasive
model output as experimental evidence.

### General-purpose network analytics toolkit

This would permit broader feature growth, but it would weaken the falsifiable core
question and make it harder to judge whether new functionality improves anomaly
ranking research.

## Consequences

### Benefits

- The public claim matches the evidence JAWS can produce and preserve.
- Research validity, inspectability, and reproducibility outrank product polish.
- New methods can be judged by whether they improve useful rankings rather than
  whether they add surface area.
- Agents and MCP remain useful interfaces without becoming the scientific core.

### Costs and limitations

- JAWS will not promise the operational certainty some security users expect from
  an IDS or commercial threat product.
- Research-grade provenance, benchmark governance, and evidence retention add
  implementation work that a demonstration-only toolkit could avoid.
- “Meaningful” anomalies ultimately require labels or researcher interpretation;
  ranking metrics cannot manufacture ground truth.

### Follow-on constraints

- User-facing language must distinguish score, rank, outlier flag, anomaly, and
  malicious verdict.
- Every finding must retain a path to supporting evidence.
- Software-correctness claims and ranking-quality claims must be evaluated
  separately.
- A proposed feature should identify the research question or workflow it serves.

## Benchmark impact

Benchmarks must evaluate ordered investigative usefulness, including full
rankings, recall at investigator-relevant cutoffs, reciprocal rank, benign burden,
stability, explanation fidelity, and cost. Classification accuracy alone is not a
sufficient project-level metric. Synthetic results must be identified as such and
cannot establish general detector quality.

## Migration

Documentation and interfaces will adopt research-workbench terminology while
legacy CLI entry points remain available until adapter parity and migration
documentation exist. Existing detector behavior is preserved initially as a
named legacy ranker rather than rebranded as a threat verdict.

## Reversal conditions

Revisit this charter only if the project intentionally expands into an
operational detection or response system with evidence for calibrated verdicts,
defined service expectations, and an updated governance model. Reversal requires
a superseding ADR, a project-plan revision, and benchmarks appropriate to the new
claims.

## References

- [JAWS README](../../README.md)
- [JAWS research workbench implementation plan](../../IMPLEMENTATION_PLAN.md)

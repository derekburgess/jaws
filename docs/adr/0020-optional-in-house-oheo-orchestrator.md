# ADR-0020: Optional in-house OHEO orchestrator

## Status

Accepted for the experimental laboratory

## Context

Milestone 9 needs one bounded Orient → Hypothesize → Experiment → Observe cycle, not a
general-purpose autonomous coding agent. The framework must preserve typed state, traces,
isolation, reproducibility, low dependency weight, and model portability.

| Candidate | Typed state | Trace/durability | Isolation | Dependency/maintenance fit | Model portability |
| --- | --- | --- | --- | --- | --- |
| Small in-house state machine | Standard-library dataclasses/protocols | Canonical local trace; MCP run journals/bundles | Separate capability-free container | Smallest surface; JAWS owns it | Provider-neutral model protocol |
| NOOA | Typed inputs/outputs and explicit object state | Optional CLI tracing/evaluation | Documentation explicitly requires OS sandboxing for generated-code agents | Emerging Git-main dependency; code-as-action exceeds this lab's needs | Model-agnostic |
| LangGraph | Typed graph state by application convention | Mature checkpoints, interrupts, replay, optional LangSmith | Must still be supplied by deployment | Larger graph/runtime surface | Broad model ecosystem |
| Pydantic AI | Generic typed agent dependencies/output | Instrumentation plus Temporal/DBOS/Prefect/Restate durability integrations | Must still be supplied by deployment | Adds Pydantic/provider framework and optional durable runtime | Broad provider support |

The comparison uses the current primary documentation: [NOOA](https://github.com/NVIDIA-NeMo/labs-OO-Agents),
[LangGraph persistence](https://docs.langchain.com/oss/python/langgraph/persistence), and
[Pydantic AI durable execution](https://pydantic.dev/docs/ai/capabilities/durable_execution/overview/).

## Decision

Use a small in-house orchestrator behind `ResearchGateway` and `ResearchModel` protocols for
the sandboxed spike. NOOA remains a candidate, not a dependency: its typed object model is
promising, but code-as-action is deliberately outside this laboratory's authority. LangGraph
or Pydantic AI becomes justified only if the process-local spike needs durable distributed
resumption or multiple provider-driven branches.

All framework code lives under optional `jaws_lab`. Core, CLI, MCP, and benchmarks never import
it. The scripted reference model has no provider dependency and proves the cycle and policy
before any real model receives authority.

## Consequences

The laboratory has less framework machinery but a much smaller attack/dependency surface.
Canonical experiment services calculate every score and reward. The model proposes documents
and narrative only; validation, approvals, budgets, held-out policy, citations, redaction, and
ground-truth restrictions remain deterministic code. Removing `jaws_lab`, its entry point, and
its container leaves all core schemas and behavior unchanged.

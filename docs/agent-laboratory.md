# Optional agent laboratory

The laboratory evaluates a research collaborator; it is not a detector and never supplies
ground truth. Its fixed cycle is Orient → Hypothesize → Experiment → Observe. Proposals pass
through the same catalog and versioned contracts as MCP/CLI, while deterministic services
calculate rankings, metrics, rewards, bundles, and observation deltas.

## Run the scripted spike

First obtain the canonical experiment identity:

```bash
jaws-lab identity examples/research/control-treatment.json
```

Then provide the exact mutation approval returned by the identity command:

```bash
jaws-lab run \
  --catalog examples/research/catalog.json \
  --experiment examples/research/control-treatment.json \
  --root .jaws-lab \
  --confirmation approve:experiment_start:EXACT_EXPERIMENT_ID
```

The retained trace records framework/model/provider and prompt versions, bounded tool calls,
approval digests, usage/cost totals, proposed specs, run/observation citations, metrics,
evidence pointers, limitations, and the motivated follow-up. It omits packet content and
redacts credential-shaped keys/text.

The isolated profile builds with `compose.agent.yml`. It is non-root, read-only,
capability-free, network-disabled, PID/CPU/memory bounded, and has only its own artifact volume.
It receives no database credentials, raw evidence, capture device, Docker socket, or provider
secret. External provider/network use would require a separately reviewed overlay plus exact
cost approval; it is not part of the reference spike.

## Policy boundary

- No shell, generated Python, direct database/Cypher, deletion, host paths, packet capture,
  held-out labels, ranker/evaluator modification, or threat-ground-truth assertion.
- Live capture, dataset acquisition, external cost, and mutation are denied unless both server
  policy and exact human confirmation enable the named action/target. The shipped server has no
  live-capture or dataset-acquisition operation; experiments require exact approval.
- Dataset/benchmark metadata is untrusted. Instruction-like strings are withheld before model
  context construction, and proposed IDs/components are still catalog validated.
- Evaluation scores specification validity, falsifiability, completion, evidence citation,
  budget adherence, repeated-run consistency, fixed-study comparison, and optional human
  usefulness. A positive metric delta alone is never success.

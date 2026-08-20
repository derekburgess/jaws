# Benchmark v1 governance

Benchmark v1 exists to compare how representations, references, and rankers allocate finite investigator attention. It does not turn a synthetic score into a production-detection claim. The machine policy is versioned at `benchmarks/v1/policy.json`; this document defines the human review procedure around it.

## Evidence and safety

Dataset manifests version provenance independently from code. Each record declares source and bounded location, actual acquisition date (or null when not acquired), safety-review date, license and redistribution status, checksum and scope, capture host and time bounds where known, label-source version and provenance, partitions, expected behavior, and limitations. An available dataset must have both an acquisition date and verified checksum. Synthetic traffic is explicitly model-authored.

Only traffic evidence and labels needed for the study may be acquired. Do not acquire, store, execute, unpack, or redistribute malware binaries. External records are metadata-only until the current primary-source terms and handling risk have been reviewed. Source terms override this repository's metadata summary. Raw evidence stays outside the repository and is referenced by digest.

Routine development and validation runs do not expose held-out labels. `DatasetManifest.tuning_view()` removes those labels, and the runner skips held-out cells unless an evaluator deliberately enables them.

## Results and claims

Every comparative report must include `seeded_random` and `total_bytes`. All reward components remain in the retained JSON even when a declared scalar objective is useful for search. Aggregate values link to their contributing run IDs, and each run links to checksummed artifacts and provenance.

Expected failures are environmental or capability outcomes declared before a run, such as an external capture not being acquired or an embedding ranker abstaining when embeddings are absent. They remain visible as missing or abstained cells. Accepted regressions are metric degradations deliberately approved after review; they require an owner, rationale, expiry/review date, and an entry in `accepted_regressions`. The two categories must never overlap.

Regression budgets are declared per family and metric before inspecting a proposed change. A claim based only on improved aggregate recall is invalid when benign burden rises or coverage falls. Reviewers must inspect per-scenario rankings, paired uncertainty, false-positive family movement, explanation fidelity, runtime/cost, and missing/skipped/failed/abstained coverage. Passing a budget permits comparison; it does not prove operational usefulness.

## Tiers and automation

The smoke tier runs two development scenarios, every ranker, two seeds, and one window. It is visible on every change but report-only: harness or schema failures fail the job, while observed metric changes do not yet block merges. The full tier runs development and validation scenarios with five seeds and three windows on a scheduled or manual workflow. The ordinary correctness suite remains blocking. Threshold enforcement may become blocking only after budgets have demonstrated stable behavior and the policy change is reviewed.

Stochastic rankers must receive every declared seed. Deterministic rankers are still repeated to prove identical ordering. Window and parameter changes are part of cache identity and paired-sample identity.

## Held-out lifecycle

1. Freeze a candidate set by evidence digest, manifest version, label-source version, and evaluation policy. Restrict labels to the designated evaluator or release process.
2. Promote it only after confirming source terms, label quality, family balance, and that no tuning report or implementation author had access to labels.
3. Run it through the explicit held-out profile once the proposal and budgets are frozen. Retain the complete report and experiment bundles.
4. Mark the set compromised if labels influenced tuning, source provenance changes, duplication/leakage is discovered, or terms no longer permit use. Stop using its result for claims; do not silently relabel it as validation.
5. Retire by preserving the manifest and retirement rationale, setting scenarios unavailable, and identifying affected reports. Add a replacement under a new dataset/scenario identity and digest; never overwrite the retired evidence identity.

## Extension boundary

Registries validate versioned metadata for representations, references, rankers, evaluators, and renderers before execution. Third-party rankers receive only immutable `BenchmarkCandidate` values, an explicit `RankerSpec`, and bounded evidence pointers. The plugin contract provides no database connection, provider credentials, environment mapping, filesystem handle, or shell executor. Extensions that need those capabilities belong in a separately reviewed adapter, not a ranker.

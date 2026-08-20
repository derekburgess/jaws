# Embedding and profile-representation contract

`ProfileRepresentationService` is the only Milestone 3 service allowed to turn endpoint
profile drafts into stored representation records. It accepts an explicit numeric feature
set, optional text template, optional `EmbeddingProvider`, observation-scope identity, and
clock. It constructs the complete replacement in memory and calls
`ProfileRepository.replace_scope` exactly once after validation.

## Provider contract

Local and remote adapters implement the same `EmbeddingProvider` protocol:

- `spec` declares provider ID, model ID and revision, whether the revision is immutable,
  expected dimensions when known, normalization, provider batch size, and device;
- `embed` accepts ordered `EmbeddingInput` records containing profile identity and exact
  input text;
- the returned `EmbeddingBatch` tags every vector with its input-text digest and records
  actual execution metadata plus request/token/cost usage.

`OpenAIEmbeddingProvider` batches remote inputs, restores API-index order, reports token
usage, and can calculate cost when the caller supplies the applicable input-token rate.
OpenAI aliases default to `runtime-unpinned` with `revision_exact=false`; callers may supply
an immutable revision when the provider exposes one. `LocalTransformerEmbeddingProvider`
records the supplied model revision/digest, exactness, batch size, normalization, and
concrete device without importing Torch or sentence-transformers into the service layer.

## Validation and lineage

Before storage replacement, the service validates that:

- returned provider/model provenance still matches the declaration;
- vector count equals input count;
- input digests remain in the original order;
- every vector has the declared common dimension;
- every component is finite.

Each validated `ProfileEmbedding` retains `profile_key`, input-text digest, and vector.
Each stored embedding-backed `EndpointProfile` carries the same vector plus
`ProfileEmbeddingProvenance`. Neo4j and portable evidence bundles preserve provider ID,
model/revision and exactness, dimensions, normalization, provider batch size, device, and
input-text digest. This makes the stored profile sufficient to reconstruct the mapping
without relying on list position or mutable provider configuration.

Provider exceptions and validation failures occur before the repository call, so an
existing scope remains intact. The Neo4j repository performs deletion and creation in its
single existing write transaction.

## Numeric-only representations

When no embedding provider is supplied, the service stores a numeric representation under
the numeric feature-set identity with no model identity, text template, vector, or optional
model-stack import. Supplying a template without a provider, or a provider without a
template, is rejected as an ambiguous representation request.

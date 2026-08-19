# Ingest service contract

`IngestService` is the deterministic boundary between packet-source adapters and capture/
packet repositories. It has no Neo4j, PyShark, filesystem, interface-discovery, privilege,
configuration, or presentation dependency. Tests, CLI, MCP, benchmarks, and future agents
can therefore invoke the same lifecycle behavior.

## Explicit input

`CaptureSpec` declares source kind/name, host perspective, content digest, capture filter,
tool versions, and optional legacy display identity. A live-interface spec requires an
explicit host `EntityId`. A PCAP spec requires its content SHA-256 and accepts either an
explicit host entity supplied by the user/dataset manifest or `None` for genuinely unknown
perspective. The service never substitutes the importer machine's address.

Packet-source adapters emit `PacketObservation` values without capture ownership. Each
observation carries its original timestamp and normalized packet fields. The service
creates one opaque capture ID and binds every observation from that invocation to it as a
`PacketRecord`; it does not replace source time with ingest time.

## Lifecycle and batching

The service registers the capture directly in its appropriate active state (`running` for
live capture, `importing` for PCAP), buffers no more than the configured positive batch
size, and delegates atomic batch writes to `PacketRepository`. Finalization runs for normal
completion, source failure, storage failure, cooperative cancellation, and keyboard
interruption:

| Outcome | State | Packet count |
| --- | --- | --- |
| Source exhausted, including zero packets | `complete` | Successfully stored packets |
| Failure after at least one successful batch | `partial` | Successfully stored packets |
| Failure before any successful write | `failed` | `0` |
| Cancellation or keyboard interruption | `cancelled` | Successfully flushed packets |

Source and storage exceptions are re-raised after finalization. Cooperative cancellation
returns the finalized cancelled record. A `CancellationSignal` is checked between packet
observations, so cancellation does not interrupt an atomic repository batch.

## Adapter follow-up

The current legacy capture command still constructs PyShark live/file sources itself. The
next Milestone 3 slice will provide separate live and PCAP adapters that emit
`PacketObservation`, then reduce `jaws-capture` to compatibility/presentation over this
service. File size/path metadata, display filters, packet-type policy, and exact tshark
acquisition metadata remain part of that adapter slice.

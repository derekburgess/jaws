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

## Packet-source adapters

`PcapPacketSource` iterates `FileCapture` with PyShark packet retention disabled and closes
the capture in a `finally` path. `LivePacketSource` owns `LiveCapture` and its privileges;
it bridges the callback API to the service iterator through a bounded queue. A quiet live
interface still terminates through PyShark's wall-clock timeout rather than waiting for a
packet to evaluate elapsed time.

`PySharkPacketParser` applies one explicit policy:

| Input | Behavior |
| --- | --- |
| IPv4 or IPv6 | Emit normalized addresses from the outermost complete decoded IP layer |
| VLAN | Ignore the link wrapper and use its decoded IP layer |
| IP-in-IP/GRE tunnel | Index the outermost complete decoded IP pair; inner layers remain future evidence detail |
| TCP or UDP | Retain valid 1–65535 ports and available text payload field |
| ICMP/ICMPv6 or other IP | Emit with null ports |
| Non-IP | Skip and count; never merge unrelated frames into a synthetic address |
| Missing/invalid IP, timestamp, protocol, or size | Skip and count as malformed |

Epoch frame time is preferred. A timezone-aware decoded frame time is the fallback; naive
time is rejected rather than interpreted in the importer's timezone. Packet parse counters
remain adapter diagnostics and do not masquerade as stored packets.

Capture and display filters are passed to their distinct PyShark parameters. When declared
through `jaws-capture`, their exact values are recorded in capture provenance (capture-only
keeps the legacy raw value; display filters use a `display=` prefix; both are explicitly
prefixed). Tool provenance records JAWS and PyShark package versions plus the first exact
line of `tshark --version`, using `unknown` when unavailable.

## Remaining ingest follow-up

`jaws-capture` now constructs the appropriate source adapter and invokes `IngestService`;
it no longer parses packets, batches repository writes, or finalizes lifecycle state.
Portable file size/path/source metadata still needs a versioned persistence contract. The
remaining CLI compatibility checklist also covers `jaws-ipinfo` and `jaws-compute`, which
have not yet moved onto their Milestone 3 services.

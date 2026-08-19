"""Deterministic capture lifecycle and bounded packet-ingest coordination."""

from __future__ import annotations

from dataclasses import dataclass

from jaws.domain import (
    CaptureId,
    CaptureRecord,
    CaptureSpec,
    CaptureState,
    Clock,
    IdGenerator,
    PacketObservation,
    PacketRecord,
)
from jaws.ports import (
    CancellationSignal,
    CaptureRepository,
    PacketRepository,
    PacketSource,
)


@dataclass(frozen=True, slots=True)
class IngestService:
    """Ingest one bounded source into one explicit, always-finalized capture session."""

    captures: CaptureRepository
    packets: PacketRepository
    clock: Clock
    capture_ids: IdGenerator[CaptureId]
    batch_size: int = 100

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError("ingest batch_size must be positive")

    def ingest(
        self,
        spec: CaptureSpec,
        source: PacketSource[PacketObservation],
        *,
        cancellation: CancellationSignal | None = None,
    ) -> CaptureRecord:
        """Store a complete bounded source or finalize its exact partial outcome."""

        capture_id = self.capture_ids.new()
        registered_at = self.clock.now()
        legacy_capture_id = spec.legacy_capture_id or registered_at.strftime("%Y%m%dT%H%M%SZ")
        registered = CaptureRecord(
            capture_id=capture_id,
            source_kind=spec.source_kind,
            source_name=spec.source_name,
            state=CaptureState.REGISTERED,
            registered_at=registered_at,
            legacy_capture_id=legacy_capture_id,
            content_digest=spec.content_digest,
            perspective=spec.perspective,
            capture_filter=spec.capture_filter,
            tool_versions=spec.tool_versions,
        )
        active = registered.transition(spec.active_state, self.clock.now())
        self.captures.add(active)

        batch: list[PacketRecord] = []
        stored_packet_count = 0
        caught: BaseException | None = None
        cancelled = False
        final: CaptureRecord

        def flush() -> None:
            nonlocal stored_packet_count
            if not batch:
                return
            expected = len(batch)
            written = self.packets.append(capture_id, tuple(batch))
            batch.clear()
            if (
                not isinstance(written, int)
                or isinstance(written, bool)
                or not 0 <= written <= expected
            ):
                raise RuntimeError(
                    f"packet repository reported invalid write count {written!r} "
                    f"for a {expected}-packet batch"
                )
            stored_packet_count += written
            if written != expected:
                raise RuntimeError(
                    f"packet repository reported {written} writes for a {expected}-packet batch"
                )

        try:
            for observation in source.packets():
                if cancellation is not None and cancellation.is_cancelled():
                    cancelled = True
                    break
                batch.append(observation.for_capture(capture_id))
                if len(batch) >= self.batch_size:
                    flush()
            if cancellation is not None and cancellation.is_cancelled():
                cancelled = True
        except KeyboardInterrupt as error:
            cancelled = True
            caught = error
        except BaseException as error:
            caught = error
        finally:
            try:
                flush()
            except Exception as error:
                if caught is None:
                    caught = error

            failure_code: str | None
            if cancelled:
                terminal_state = CaptureState.CANCELLED
                failure_code = "ingest_cancelled"
            elif caught is None:
                terminal_state = CaptureState.COMPLETE
                failure_code = None
            else:
                terminal_state = (
                    CaptureState.PARTIAL if stored_packet_count else CaptureState.FAILED
                )
                failure_code = type(caught).__name__
            final = active.transition(
                terminal_state,
                self.clock.now(),
                packet_count=stored_packet_count,
                failure_code=failure_code,
            )
            self.captures.transition(final, expected_state=active.state)

        if caught is not None:
            raise caught
        return final

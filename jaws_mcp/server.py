"""Thin MCP v2 transport adapter over the bounded research application facade."""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Literal, Protocol, TypeVar, cast

from jaws.adapters.research_api import (
    ResearchAPIError,
    ResearchApplication,
    failure,
)
from jaws.research_codec import load_catalog
from jaws.services.benchmark_rankers import default_benchmark_registries
from jaws.services.research import ComponentDescriptor, ResearchCatalog
from jaws_mcp.contracts import TOOL_CONTRACTS

FunctionT = TypeVar("FunctionT", bound=Callable[..., Any])


class _MCPServerProtocol(Protocol):
    def tool(self, **_kwargs: object) -> Callable[[FunctionT], FunctionT]: ...

    def run(self, **_kwargs: object) -> None: ...


class _UnavailableMCPServer:
    def __init__(self, *_args: object, **_kwargs: object) -> None:
        pass

    def tool(self, **_kwargs: object) -> Callable[[FunctionT], FunctionT]:
        return lambda function: function

    def run(self, **_kwargs: object) -> None:
        raise RuntimeError("install the 'mcp' extra to serve MCP transports")


try:
    from mcp.server import MCPServer as _SDKMCPServer  # type: ignore[import-not-found]
except ImportError:
    _SDKMCPServer = _UnavailableMCPServer

INSTRUCTIONS = """JAWS MCP v2 is a bounded research interface.

Orient first, propose a falsifiable hypothesis, validate an ExperimentSpec, then use the
start/status/result/cancel lifecycle for long work. Small deterministic stages may use the
exploratory operation. Findings include structured evidence pointers accepted by
inspect_evidence. Capture and destructive administration are not exposed by this server.
"""

mcp = cast(
    _MCPServerProtocol,
    _SDKMCPServer("JAWS Research Workbench", instructions=INSTRUCTIONS),
)
_application: ResearchApplication | None = None


def set_application(application: ResearchApplication | None) -> None:
    """Set an injected application facade for tests or an operator composition root."""

    global _application
    _application = application


def application() -> ResearchApplication:
    global _application
    if _application is None:
        catalog_path = os.environ.get("JAWS_MCP_CATALOG")
        catalog = load_catalog(Path(catalog_path)) if catalog_path else _default_catalog()
        artifact_root = Path(os.environ.get("JAWS_ARTIFACT_ROOT", ".jaws-research")) / "mcp-v2"
        _application = ResearchApplication(catalog, artifact_root)
    return _application


def _default_catalog() -> ResearchCatalog:
    catalog = ResearchCatalog()
    registries = default_benchmark_registries()
    for metadata in (
        *registries.representations.list(),
        *registries.references.list(),
        *registries.rankers.list(),
        *registries.evaluators.list(),
        *registries.renderers.list(),
    ):
        catalog.register(
            ComponentDescriptor(
                metadata.kind,
                metadata.component_id,
                metadata.version,
                compatible_representations=getattr(metadata, "compatible_representations", ()),
            )
        )
    return catalog


def _call(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    try:
        return operation()
    except ResearchAPIError as error:
        return failure(error)
    except Exception:
        return failure(
            ResearchAPIError(
                "operation_unavailable",
                "research operation failed at the application boundary",
                retryable=True,
            )
        )


@mcp.tool(
    **{key: TOOL_CONTRACTS["research_capabilities"][key] for key in ("description",)},
    name="research_capabilities",
)
def research_capabilities() -> dict[str, Any]:
    return _call(application().capabilities)


@mcp.tool(
    **{key: TOOL_CONTRACTS["research_orient"][key] for key in ("description",)},
    name="research_orient",
)
def research_orient() -> dict[str, Any]:
    return _call(application().orient)


@mcp.tool(
    **{key: TOOL_CONTRACTS["dataset_import"][key] for key in ("description",)},
    name="dataset_import",
)
def dataset_import(dataset_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    return _call(lambda: application().resource_operation("dataset_import", dataset_id, options))


@mcp.tool(
    **{key: TOOL_CONTRACTS["capture_enrich"][key] for key in ("description",)},
    name="capture_enrich",
)
def capture_enrich(capture_id: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    return _call(lambda: application().resource_operation("capture_enrich", capture_id, options))


@mcp.tool(
    **{key: TOOL_CONTRACTS["capture_profile"][key] for key in ("description",)},
    name="capture_profile",
)
def capture_profile(
    capture_id: str,
    representation_id: str,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    parameters = {"representation_id": representation_id, **(options or {})}
    return _call(
        lambda: application().resource_operation("capture_profile", capture_id, parameters)
    )


@mcp.tool(
    **{key: TOOL_CONTRACTS["validate_hypothesis"][key] for key in ("description",)},
    name="validate_hypothesis",
)
def validate_hypothesis(document: dict[str, Any]) -> dict[str, Any]:
    return _call(lambda: application().validate_hypothesis(document))


@mcp.tool(
    **{key: TOOL_CONTRACTS["validate_experiment"][key] for key in ("description",)},
    name="validate_experiment",
)
def validate_experiment(document: dict[str, Any]) -> dict[str, Any]:
    return _call(lambda: application().validate_experiment(document))


@mcp.tool(
    **{key: TOOL_CONTRACTS["explore_operation"][key] for key in ("description",)},
    name="explore_operation",
)
def explore_operation(
    document: dict[str, Any],
    variant: str,
    stage: Literal["represent", "reference", "rank", "evaluate"],
) -> dict[str, Any]:
    return _call(lambda: application().explore(document, variant, stage))


@mcp.tool(
    **{key: TOOL_CONTRACTS["experiment_start"][key] for key in ("description",)},
    name="experiment_start",
)
def experiment_start(document: dict[str, Any]) -> dict[str, Any]:
    return _call(lambda: application().start_experiment(document))


@mcp.tool(
    **{key: TOOL_CONTRACTS["experiment_status"][key] for key in ("description",)},
    name="experiment_status",
)
def experiment_status(job_id: str) -> dict[str, Any]:
    return _call(lambda: application().experiment_status(job_id))


@mcp.tool(
    **{key: TOOL_CONTRACTS["experiment_result"][key] for key in ("description",)},
    name="experiment_result",
)
def experiment_result(job_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
    return _call(lambda: application().experiment_result(job_id, offset=offset, limit=limit))


@mcp.tool(
    **{key: TOOL_CONTRACTS["experiment_cancel"][key] for key in ("description",)},
    name="experiment_cancel",
)
def experiment_cancel(job_id: str) -> dict[str, Any]:
    return _call(lambda: application().cancel_experiment(job_id))


@mcp.tool(
    **{key: TOOL_CONTRACTS["inspect_evidence"][key] for key in ("description",)},
    name="inspect_evidence",
)
def inspect_evidence(
    pointer: dict[str, Any], peer_limit: int = 20, packet_limit: int = 50
) -> dict[str, Any]:
    return _call(
        lambda: application().inspect_evidence(
            pointer, peer_limit=peer_limit, packet_limit=packet_limit
        )
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jaws-mcp")
    transports = parser.add_mutually_exclusive_group()
    transports.add_argument("--stdio", action="store_true")
    transports.add_argument("--http", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.stdio:
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

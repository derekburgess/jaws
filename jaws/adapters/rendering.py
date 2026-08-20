"""Optional plot rendering from retained, digestible result artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from jaws.domain import PlotArtifactMetadata, canonical_digest
from jaws.optional_dependencies import require_module

RENDERER_ID = "jaws_matplotlib_plotille"
RENDERER_VERSION = "1"


@dataclass(frozen=True, slots=True)
class RenderedPlot:
    metadata: PlotArtifactMetadata
    terminal: str | None = None


def _metadata(kind: str, payload: object, path: Path | None) -> PlotArtifactMetadata:
    return PlotArtifactMetadata(
        kind=kind,
        input_digest=canonical_digest(payload),
        renderer_id=RENDERER_ID,
        renderer_version=RENDERER_VERSION,
        path=str(path) if path else None,
    )


def render_port_size(rows: list[dict[str, int]], output: Path) -> RenderedPlot:
    plt = require_module("matplotlib.pyplot", "plotting", "Plot rendering")
    plotille = require_module("plotille", "plotting", "Terminal plot rendering")
    sizes = [row["size"] for row in rows]
    source_ports = [row["src_port"] for row in rows]
    destination_ports = [row["dst_port"] for row in rows]
    plt.figure(figsize=(8, 7))
    plt.scatter(sizes, source_ports, marker=">", alpha=0.3)
    plt.scatter(sizes, destination_ports, marker="<", alpha=0.3)
    plt.xlabel("Packet size (bytes)")
    plt.ylabel("Port")
    plt.tight_layout()
    path = output / "size_over_ports.png"
    plt.savefig(path, dpi=90)
    figure = plotille.Figure()
    figure.width = 80
    figure.height = 20
    figure.x_label = "SIZE"
    figure.y_label = "PORT"
    for row in rows:
        figure.scatter([row["size"]], [row["src_port"]], marker=">")
        figure.scatter([row["size"]], [row["dst_port"]], marker="<")
    return RenderedPlot(_metadata("port_size", rows, path), figure.show(legend=False))


def render_k_distance(distances: tuple[float, ...], output: Path) -> RenderedPlot:
    plt = require_module("matplotlib.pyplot", "plotting", "Plot rendering")
    plotille = require_module("plotille", "plotting", "Terminal plot rendering")
    plt.figure(figsize=(8, 7))
    plt.plot(range(len(distances)), distances)
    plt.xlabel("Index")
    plt.ylabel("K-distance")
    plt.tight_layout()
    path = output / "k_distances.png"
    plt.savefig(path, dpi=90)
    figure = plotille.Figure()
    figure.width = 80
    figure.height = 20
    figure.x_label = "INDEX"
    figure.y_label = "K-DISTANCE"
    figure.plot(list(range(len(distances))), list(distances), marker="o", lc=40)
    return RenderedPlot(_metadata("k_distance", distances, path), figure.show(legend=False))


def render_clusters(
    coordinates: np.ndarray,
    labels: tuple[int, ...],
    annotations: list[dict[str, Any]],
    output: Path,
) -> RenderedPlot:
    plt = require_module("matplotlib.pyplot", "plotting", "Plot rendering")
    if coordinates.ndim != 2 or coordinates.shape[1] != 2 or len(coordinates) != len(labels):
        raise ValueError("cluster rendering requires one two-dimensional point per label")
    label_array = np.asarray(labels)
    clustered = label_array != -1
    plt.figure(figsize=(8, 7))
    plt.scatter(
        coordinates[clustered, 0], coordinates[clustered, 1], c=label_array[clustered],
        cmap="winter", marker="^", alpha=0.2,
    )
    plt.scatter(
        coordinates[~clustered, 0], coordinates[~clustered, 1], color="red", marker="o"
    )
    for index, row in enumerate(annotations):
        plt.annotate(str(row.get("ip_address", "")), tuple(coordinates[index]), fontsize=6)
    plt.tight_layout()
    path = output / "pca_dbscan_outliers.png"
    plt.savefig(path, dpi=90)
    payload = {
        "coordinates": coordinates.tolist(),
        "labels": labels,
        "annotations": annotations,
    }
    return RenderedPlot(_metadata("pca_dbscan", payload, path))

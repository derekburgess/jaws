"""Optional plot rendering from retained, digestible result artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from jaws.domain import (
    ClusterPlotData,
    KDistancePlotData,
    PlotArtifactMetadata,
    PortSizePlotData,
    canonical_digest,
)
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


def render_port_size(data: PortSizePlotData, output: Path) -> RenderedPlot:
    plt = require_module("matplotlib.pyplot", "plotting", "Plot rendering")
    plotille = require_module("plotille", "plotting", "Terminal plot rendering")
    sizes = [row[0] for row in data.rows]
    source_ports = [row[1] for row in data.rows]
    destination_ports = [row[2] for row in data.rows]
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
    for size, source, destination in data.rows:
        figure.scatter([size], [source], marker=">")
        figure.scatter([size], [destination], marker="<")
    return RenderedPlot(_metadata("port_size", data, path), figure.show(legend=False))


def render_k_distance(data: KDistancePlotData, output: Path) -> RenderedPlot:
    plt = require_module("matplotlib.pyplot", "plotting", "Plot rendering")
    plotille = require_module("plotille", "plotting", "Terminal plot rendering")
    plt.figure(figsize=(8, 7))
    plt.plot(range(len(data.distances)), data.distances)
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
    figure.plot(list(range(len(data.distances))), list(data.distances), marker="o", lc=40)
    return RenderedPlot(_metadata("k_distance", data, path), figure.show(legend=False))


def render_clusters(
    data: ClusterPlotData,
    output: Path,
) -> RenderedPlot:
    plt = require_module("matplotlib.pyplot", "plotting", "Plot rendering")
    coordinates = np.asarray(data.coordinates)
    label_array = np.asarray(data.labels)
    clustered = label_array != -1
    plt.figure(figsize=(8, 7))
    plt.scatter(
        coordinates[clustered, 0], coordinates[clustered, 1], c=label_array[clustered],
        cmap="winter", marker="^", alpha=0.2,
    )
    plt.scatter(
        coordinates[~clustered, 0], coordinates[~clustered, 1], color="red", marker="o"
    )
    for index, row in enumerate(data.annotations):
        plt.annotate(str(row.get("ip_address", "")), tuple(coordinates[index]), fontsize=6)
    plt.tight_layout()
    path = output / "pca_dbscan_outliers.png"
    plt.savefig(path, dpi=90)
    return RenderedPlot(_metadata("pca_dbscan", data, path))

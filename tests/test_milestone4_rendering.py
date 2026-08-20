"""Visualization adapters render only retained domain artifacts."""

from jaws.adapters import rendering
from jaws.domain import ClusterPlotData, KDistancePlotData, PortSizePlotData


class _Plot:
    def __getattr__(self, _name):
        return lambda *_args, **_kwargs: None


class _Figure:
    def __init__(self):
        self.width = 0
        self.height = 0
        self.x_label = ""
        self.y_label = ""

    def scatter(self, *_args, **_kwargs):
        pass

    def plot(self, *_args, **_kwargs):
        pass

    def show(self, **_kwargs):
        return "terminal-artifact"


class _Plotille:
    Figure = _Figure


def test_renderers_record_stable_input_digest_and_version(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rendering,
        "require_module",
        lambda name, *_args: _Plot() if name == "matplotlib.pyplot" else _Plotille(),
    )
    port_data = PortSizePlotData(((100, 50000, 443),))
    distance_data = KDistancePlotData((0.1, 0.2, 0.9))
    cluster_data = ClusterPlotData(
        coordinates=((0.0, 0.0), (1.0, 1.0)),
        labels=(0, -1),
        annotations=({"ip_address": "192.0.2.1"}, {"ip_address": "198.51.100.2"}),
    )
    first = rendering.render_port_size(port_data, tmp_path)
    second = rendering.render_port_size(port_data, tmp_path)
    distance = rendering.render_k_distance(distance_data, tmp_path)
    cluster = rendering.render_clusters(cluster_data, tmp_path)
    assert first.metadata.input_digest == second.metadata.input_digest
    assert first.metadata.renderer_version == "1"
    assert first.terminal == "terminal-artifact"
    assert {first.metadata.kind, distance.metadata.kind, cluster.metadata.kind} == {
        "port_size",
        "k_distance",
        "pca_dbscan",
    }

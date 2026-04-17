import os
import re

import matplotlib.pyplot as plt
import networkx as nx
import yaml

CSV_PATH = os.path.join(
    os.path.dirname(__file__),
    "../../sds_data_manager/lambda_code/SDSCode/pipeline_lambdas/dependency_config.csv",
)
CONFIG_DIR = os.path.join(os.path.dirname(__file__), "config")


def _level_sort_key(level: str) -> tuple:
    """Return a sort key so that l0 < l1 < l1a < l1b < l2 < l3a < ..."""
    m = re.match(r"l(\d+)([a-z]*)", level)
    if not m:
        return (999, level)
    return (int(m.group(1)), m.group(2))


def build_graphs(csv_path: str) -> dict[str, nx.DiGraph]:
    """
    Return one DiGraph per instrument. Nodes are (level, desc) tuples;
    edges point from input to output for intra-instrument HARD DOWNSTREAM
    data-level dependencies only.
    """
    graphs: dict[str, nx.DiGraph] = {}

    with open(csv_path) as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 8:
                raise RuntimeError("expected 8 columns")
            (
                pri_src,
                pri_type,
                pri_desc,
                dep_src,
                dep_type,
                dep_desc,
                relationship,
                dep_kind,
            ) = parts

            if dep_kind != "DOWNSTREAM":
                raise RuntimeError("expected DOWNSTREAM dependency kind")

            if dep_src not in graphs:
                graphs[dep_src] = nx.DiGraph()
            graphs[dep_src].add_edge((pri_type, pri_desc), (dep_type, dep_desc))

    return graphs


def check_no_cycles(instrument: str, graph: nx.DiGraph) -> None:
    """Raise if the graph contains a cycle."""
    try:
        cycle = nx.find_cycle(graph)
        raise RuntimeError(f"{instrument}: cycle detected: {cycle}")
    except nx.NetworkXNoCycle:
        pass


def check_single_paths(instrument: str, graph: nx.DiGraph) -> None:
    """Raise if any two nodes are connected by more than one directed path."""
    if not nx.is_forest(graph.to_undirected()):
        # Find a concrete example of a node with multiple paths from some ancestor.
        for node in graph.nodes():
            for ancestor in nx.ancestors(graph, node):
                paths = list(nx.all_simple_paths(graph, ancestor, node))
                if len(paths) > 1:
                    print(
                        f"{instrument}: multiple paths from {ancestor} to {node}: {paths}"
                    )


def all_edges(graph: nx.DiGraph) -> list[tuple[str, str, str, str]]:
    """Return all edges as (in_level, in_desc, out_level, out_desc) tuples."""
    result = [
        (in_level, in_desc, out_level, out_desc)
        for (in_level, in_desc), (out_level, out_desc) in graph.edges()
    ]
    result.sort(key=lambda r: (_level_sort_key(r[2]), r[3]))
    return result


def write_png(instrument: str, graph: nx.DiGraph) -> None:
    try:
        pos = nx.nx_agraph.graphviz_layout(
            graph, prog="dot", args="-Granksep=2.5 -Gnodesep=1.5"
        )
    except Exception:
        pos = nx.spring_layout(graph, k=3.0, iterations=100)

    labels = {node: f"{node[0]}\n{node[1]}" for node in graph.nodes()}
    n = max(graph.number_of_nodes(), 1)
    fig, ax = plt.subplots(figsize=(max(14, n * 2), max(10, n)))
    nx.draw_networkx_nodes(
        graph, pos=pos, ax=ax, node_size=3000, node_color="lightblue"
    )
    nx.draw_networkx_labels(graph, pos=pos, labels=labels, ax=ax, font_size=8)
    nx.draw_networkx_edges(
        graph,
        pos=pos,
        ax=ax,
        arrows=True,
        arrowsize=20,
        width=1.5,
        edge_color="gray",
        connectionstyle="arc3,rad=0.1",
        min_source_margin=30,
        min_target_margin=30,
    )
    ax.set_title(instrument, fontsize=14)
    ax.axis("off")
    out_path = os.path.join(CONFIG_DIR, f"{instrument}.png")
    fig.savefig(out_path, bbox_inches="tight", dpi=150)
    plt.close(fig)
    print(f"Wrote {out_path}")


def write_yaml(instrument: str, pairs: list[tuple[str, str, str, str]]) -> None:
    levels = [
        {
            "input": {"level": in_level, "desc": in_desc},
            "output": {"level": out_level, "desc": out_desc},
        }
        for in_level, in_desc, out_level, out_desc in pairs
    ]
    data = {"levels": levels}
    out_path = os.path.join(CONFIG_DIR, f"{instrument}.yaml")
    with open(out_path, "w") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)
    print(f"Wrote {out_path} ({len(levels)} entries)")


def main() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    graphs = build_graphs(CSV_PATH)
    for instrument in sorted(graphs):
        if instrument != "lo":
            continue
        check_no_cycles(instrument, graphs[instrument])
        check_single_paths(instrument, graphs[instrument])
        pairs = all_edges(graphs[instrument])
        write_yaml(instrument, pairs)
        write_png(instrument, graphs[instrument])


if __name__ == "__main__":
    main()

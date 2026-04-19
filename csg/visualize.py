# -*- coding: utf-8 -*-
"""
visualize.py — Render an interactive knowledge graph (HTML) with PyVis

- Uses NetworkX DiGraph for layout metrics (degree/betweenness centrality)
- Edge colors controlled by a mapping (rel_type -> color) from config.yaml
- PyVis cdn_resources='in_line' so the HTML is fully offline-capable
"""

from __future__ import annotations
from typing import List, Tuple, Dict, Any

import networkx as nx
from pyvis.network import Network


def render_graph_html(
    nodes: List[str],
    rel_edges: List[Tuple[str, str, Dict[str, Any]]],
    inter_edges: List[Tuple[str, str, Dict[str, Any]]],
    out_html: str,
    edge_colors: Dict[str, str],
):
    """
    Args:
        nodes: list of person names
        rel_edges: list of (src, dst, {"type": rel_type, "weight": float})
        inter_edges: list of (src, dst, {"type": i_type, "weight": float})
        out_html: path to write HTML
        edge_colors: mapping from relation type to hex color (e.g., "#ff9900")
    """
    # Build a relation-only DiGraph for centrality (simpler narrative)
    G = nx.DiGraph()
    G.add_nodes_from(nodes)
    for u, v, d in rel_edges:
        weight = float(d.get("weight", 1.0) or 0.0)
        G.add_edge(u, v, weight=weight, type=d.get("type", "UNKNOWN"))

    # Centrality metrics (safe for empty graphs)
    deg_cen = nx.degree_centrality(G) if G.number_of_nodes() > 0 else {}
    bet_cen = nx.betweenness_centrality(G, normalized=True) if G.number_of_nodes() > 0 else {}

    # Build PyVis network
    net = Network(height="850px", width="100%", directed=True, notebook=False, cdn_resources="in_line")
    net.toggle_physics(True)
    net.show_buttons(filter_=['physics'])

    # Add nodes with tooltips
    for n in G.nodes():
        title = f"{n}<br>degree: {deg_cen.get(n, 0):.3f}<br>betweenness: {bet_cen.get(n, 0):.3f}"
        net.add_node(n, label=n, title=title)

    # Add relation edges with color & weight
    for u, v, d in rel_edges:
        etype = d.get("type", "UNKNOWN")
        color = edge_colors.get(etype, "#666")
        weight = max(float(d.get("weight", 1.0) or 1.0), 0.1)
        net.add_edge(u, v, value=weight, color=color, title=etype)

    # Optional: overlay interactions (uncomment to show)
    for u, v, d in inter_edges:
        itype = d.get("type", "INTERACTION")
        weight = max(float(d.get("weight", 1.0) or 1.0), 0.1)
        net.add_edge(u, v, value=weight, color="rgba(120,120,120,0.35)", title=itype, dashes=True)

    # Avoid auto-opening a browser; just write the HTML file
    # Use explicit UTF-8 to handle non-ASCII characters (e.g. Korean script)
    html_content = net.html
    with open(out_html, "w", encoding="utf-8") as fh:
        fh.write(html_content)
    return out_html

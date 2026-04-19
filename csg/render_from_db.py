import sqlite3
from graph_store import GraphStore
from visualize import render_graph_html

DB_PATH = "out/graph.db"
OUTPUT = "out/graph_from_db.html"

store = GraphStore(DB_PATH)
nodes, rel_edges, inter_edges = store.read_graph()
store.close()

edge_colors = {
    "FAMILY": "#ff7f0e",
    "ROMANTIC": "#e41a1c",
    "FRIEND": "#1f77b4",
    "ALLY": "#17becf",
    "BOSS_OF": "#2ca02c",
    "SUBORDINATE_OF": "#98df8a",
    "TEACHER_OF": "#8c564b",
    "STUDENT_OF": "#c49c94",
    "RIVAL": "#9467bd",
    "ENEMY": "#d62728",
    "BETRAYAL": "#e377c2",
    "PROTECTS": "#7f7f7f",
    "BLACKMAILS": "#bcbd22",
    "OWES_DEBT": "#8c6d31",
    "COAUTHOR": "#6baed6",
    "COCONSPIRATOR": "#9edae5",
    "UNKNOWN": "#666666"
}

render_graph_html(nodes, rel_edges, inter_edges, OUTPUT, edge_colors)
print("Graph regenerated at:", OUTPUT)

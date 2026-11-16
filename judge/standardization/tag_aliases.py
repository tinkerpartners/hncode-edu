"""Canonical tag resolution (spec F06, Appendix A2).

``resolve_tags`` maps model-proposed names onto existing ``ProblemType`` rows
by ``name`` or ``full_name`` (case-insensitive), after alias normalisation.
It never creates a type, never returns ``chuaphanloai`` next to a real tag,
and reports what it could not resolve so the reviewer sees it.
"""

import re

from judge.models import ProblemType

UNCLASSIFIED = "chuaphanloai"

# old / synonym -> canonical name as used in Appendix A1
ALIASES = {
    "graph": "graphs",
    "graphs - đồ thị chung": "graphs",
    "shortest paths - đường đi ngắn nhất": "shortest paths",
    "implementation - cài đặt": "implementation",
    "math - toán học cơ bản": "math",
    "basic math": "math",
    "binary search - tìm kiếm nhị phân": "binary search",
    "brute force - vét cạn / trâu bò": "brute force",
    "dp - quy hoạch động cơ bản": "dp",
    "dynamic programming": "dp",
    "data structures - ctdl cơ bản": "data structures",
    "dfs and similar - duyệt đồ thị": "dfs and similar",
    "dsu - disjoint set union": "dsu",
    "disjoint set union": "dsu",
    "union find": "dsu",
    "two-pointers": "two pointers",
    "number-theory": "number theory",
    "adhoc": "ad-hoc",
    "ad hoc": "ad-hoc",
    "backtrack": "backtracking",
    "bfs-01": "0-1 bfs",
    "bridge": "bridges",
    "articulation-point": "articulation points",
    "biconnected-component": "biconnected components",
    "euler-path": "eulerian path",
    "euler-tour": "euler tour",
    "extgcd": "extended euclid",
    "flow-mincost": "min cost max flow",
    "flow-demands": "flow with lower bounds",
    "hld": "heavy-light decomposition",
    "prefix-sum": "prefix sum",
    "prefix sums": "prefix sum",
    "segment tree lazy": "lazy propagation",
    # pairs the spec leaves to the implementation
    "sorting": "sortings",
    "sort": "sortings",
    "binary indexed tree": "fenwick tree",
    "bit": "fenwick tree",
    "lowest common ancestor": "lca",
    "strongly connected components": "scc",
    "minimum spanning tree": "mst",
    "directed acyclic graph": "dag",
    "chinese remainder theorem": "crt",
    "constructive": "constructive algorithms",
    "string": "strings",
    "tree": "trees",
    "dfs": "dfs and similar",
    "bfs": "dfs and similar",
    "greedy algorithms": "greedy",
    "bitmask": "bitmasks",
    "matrix": "matrices",
    "probability": "probabilities",
    "game theory": "games",
    "chưa phân loại": UNCLASSIFIED,
}


def _norm(name):
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def canonical_name(name):
    n = _norm(name)
    return ALIASES.get(n, n)


def _catalogue():
    """{normalised name or full_name: ProblemType}"""
    table = {}
    for pt in ProblemType.objects.all():
        table.setdefault(_norm(pt.full_name), pt)
        table.setdefault(_norm(pt.name), pt)
    return table


def resolve_tags(names, catalogue=None):
    """Return (type_ids, canonical_full_names, unresolved) preserving order."""
    table = catalogue if catalogue is not None else _catalogue()
    found, unresolved, seen = [], [], set()
    for raw in names or []:
        key = canonical_name(raw)
        pt = (
            table.get(key)
            or table.get(key.replace("-", " "))
            or table.get(key.replace(" ", "-"))
        )
        if pt is None:
            unresolved.append(raw)
            continue
        if pt.id in seen:
            continue
        seen.add(pt.id)
        found.append(pt)
    # "Chưa phân loại" is never a result next to a real tag.
    if len(found) > 1:
        found = [pt for pt in found if pt.name != UNCLASSIFIED]
    return [pt.id for pt in found], [pt.full_name for pt in found], unresolved


def catalogue_names():
    """Canonical names offered to the model: full_name of every ProblemType
    except the unclassified placeholder, sorted."""
    return sorted(
        pt.full_name for pt in ProblemType.objects.all() if pt.name != UNCLASSIFIED
    )

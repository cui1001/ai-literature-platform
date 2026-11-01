"""局域引用图谱构建。

流程：读取论文 references → 与本地库匹配（arXiv / DOI / 标题）→ 回写 ``refs`` 与 ``edges``
→ 用 networkx 构建有向图 → 计算中心性 / 聚类系数 / 社区 → 识别基石、桥梁、边缘衍生论文
→ 生成带数值证据的中文洞察 → 持久化到 ``graphs`` 表。

设计取舍：

* **PageRank**（α=0.85，按边权加权）衡量「被后续工作继承」的全局重要性，比单纯入度更能
  区分「被大量低质引用」与「被关键工作引用」；
* **Betweenness** 衡量跨主题的中介作用，≤80 节点时精确计算（O(VE) 可接受），
  超过 80 节点退化为 k=80 的确定性抽样（seed=``settings.layout_seed``）；
* **聚类系数** 基于无向化后的图计算，用于判断局部引用社区是否稠密；
* 社区划分优先 ``greedy_modularity_communities``（确定、无需额外依赖），无边时回退弱连通分量。
"""

from __future__ import annotations

import statistics
from typing import Any, Mapping, Sequence

import networkx as nx

from ..core.config import Settings, get_settings
from ..core.errors import NotFoundError, ValidationError
from ..core.hashing import short_id
from ..core.logging import get_logger
from ..core.textutil import dedupe_keep_order, keyword_candidates, truncate
from ..db import repo
from ..parsing.pipeline import load_parsed
from .refmatch import LibraryIndex, RefMatch

logger = get_logger(__name__)

GRAPH_VERSION = 1
EXACT_BETWEENNESS_LIMIT = 80
FOUNDATION_PAGERANK_FACTOR = 1.3
FOUNDATION_MIN_INDEGREE = 2
BRIDGE_MIN_BETWEENNESS = 1e-9
PAGERANK_ALPHA = 0.85
BETWEENNESS_SAMPLE = 80

ROLE_LABELS_ZH = {
    "foundation": "基石论文",
    "bridge": "桥梁论文",
    "peripheral": "边缘衍生论文",
    "normal": "常规工作",
}


# ============================================================ 基础工具
def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _load_brief(paper_id: str, settings: Settings) -> dict[str, Any]:
    """论文的轻量元数据（DB 优先，解析结果兜底）。"""
    row = repo.get_paper(paper_id)
    if not row:
        raise NotFoundError(f"论文不存在：{paper_id}", detail={"paper_id": paper_id})
    parsed: dict[str, Any] = {}
    try:
        parsed = load_parsed(paper_id, settings) or {}
    except (OSError, ValueError) as exc:  # 解析文件损坏不应阻断图谱构建
        logger.warning("读取解析结果失败 paper_id=%s: %s", paper_id, exc)
    title = str(row.get("title") or parsed.get("title") or "").strip() or paper_id
    abstract = str(row.get("abstract") or parsed.get("abstract") or "").strip()
    year = _as_int(row.get("year") or parsed.get("year"), 0)
    arxiv_id = str(row.get("arxiv_id") or parsed.get("arxiv_id") or "").strip()
    return {
        "id": paper_id,
        "title": title,
        "abstract": abstract,
        "year": year,
        "arxiv_id": arxiv_id,
    }


def _edge_weight(match: RefMatch) -> float:
    if match.method in ("arxiv", "doi"):
        return 1.0
    if match.method == "title" and match.score >= 0.999:
        return 1.0
    return 0.9


def _edge_evidence(ref: Mapping[str, Any], match: RefMatch) -> str:
    ref_idx = _as_int(ref.get("ref_idx"), 0)
    label = {
        "arxiv": "arXiv ID 匹配",
        "doi": "DOI 匹配",
        "title": "标题匹配",
        "raw": "原文标题匹配",
    }.get(match.method, "引用匹配")
    head = f"{label}（ref#{ref_idx}，score {match.score:.3f}）"
    raw = str(ref.get("raw") or "").strip()
    if raw:
        head += f"：{truncate(raw, 100)}"
    return truncate(head, 260)


# ============================================================ 匹配与建图
def _match_references(paper_ids: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
    """逐篇匹配引文，回写 refs.matched_paper / match_score，并刷新 edges 表。"""
    index = LibraryIndex(repo.paper_index())
    edge_map: dict[str, list[dict[str, Any]]] = {}
    for paper_id in paper_ids:
        targets: dict[str, dict[str, Any]] = {}
        for ref in repo.get_refs(paper_id):
            ref_idx = _as_int(ref.get("ref_idx"), 0)
            match = index.match(ref, exclude=(paper_id,))
            if match is None:
                repo.update_ref(paper_id, ref_idx, matched_paper=None, match_score=None)
                continue
            repo.update_ref(
                paper_id,
                ref_idx,
                matched_paper=match.paper_id,
                match_score=round(match.score, 4),
            )
            weight = _edge_weight(match)
            evidence = _edge_evidence(ref, match)
            entry = targets.get(match.paper_id)
            if entry is None:
                targets[match.paper_id] = {
                    "dst": match.paper_id,
                    "kind": "cites",
                    "weight": weight,
                    "evidence": evidence,
                }
            else:
                entry["weight"] = max(float(entry["weight"]), weight)
                if evidence not in entry["evidence"]:
                    entry["evidence"] = truncate(f"{entry['evidence']}；另一条引用：{evidence}", 300)
        edges = sorted(targets.values(), key=lambda item: str(item["dst"]))
        repo.replace_edges(paper_id, edges)
        edge_map[paper_id] = edges
    return edge_map


def _build_digraph(paper_ids: Sequence[str]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for paper_id in paper_ids:
        graph.add_node(paper_id)
    for row in repo.get_edges(paper_ids):
        source = str(row.get("src") or "")
        target = str(row.get("dst") or "")
        if source not in graph or target not in graph or source == target:
            continue
        weight = float(row.get("weight") or 1.0)
        evidence = str(row.get("evidence") or "")
        if graph.has_edge(source, target):
            data = graph[source][target]
            data["weight"] = max(float(data.get("weight") or 1.0), weight)
            if evidence and evidence not in str(data.get("evidence") or ""):
                data["evidence"] = truncate(f"{data.get('evidence') or ''}；{evidence}", 400)
        else:
            graph.add_edge(source, target, kind=str(row.get("kind") or "cites"), weight=weight, evidence=evidence)
    return graph


# ============================================================ 图算法
def _pagerank(graph: nx.DiGraph) -> dict[str, float]:
    node_count = graph.number_of_nodes()
    if node_count == 0:
        return {}
    if graph.number_of_edges() == 0:
        uniform = 1.0 / node_count
        return {node: uniform for node in graph.nodes()}
    try:
        return dict(nx.pagerank(graph, alpha=PAGERANK_ALPHA, weight="weight", tol=1e-12, max_iter=500))
    except Exception as exc:  # 缺 scipy / 数值异常时用幂迭代兜底
        logger.warning("networkx.pagerank 失败，改用幂迭代兜底: %s", exc)
        return _power_iteration(graph)


def _power_iteration(
    graph: nx.DiGraph,
    alpha: float = PAGERANK_ALPHA,
    max_iter: int = 500,
    tol: float = 1e-12,
) -> dict[str, float]:
    nodes = list(graph.nodes())
    count = len(nodes)
    if count == 0:
        return {}
    rank = {node: 1.0 / count for node in nodes}
    out_strength = {
        node: sum(float(graph[node][nbr].get("weight") or 1.0) for nbr in graph[node]) for node in nodes
    }
    for _ in range(max_iter):
        nxt = {node: (1.0 - alpha) / count for node in nodes}
        dangling = sum(rank[node] for node in nodes if out_strength[node] <= 0.0)
        for node in nodes:
            total = out_strength[node]
            if total <= 0.0:
                continue
            for nbr in graph[node]:
                weight = float(graph[node][nbr].get("weight") or 1.0)
                nxt[nbr] += alpha * rank[node] * weight / total
        spread = alpha * dangling / count
        for node in nodes:
            nxt[node] += spread
        error = sum(abs(nxt[node] - rank[node]) for node in nodes)
        rank = nxt
        if error < tol * count:
            break
    total = sum(rank.values()) or 1.0
    return {node: value / total for node, value in rank.items()}


def _betweenness(graph: nx.DiGraph, settings: Settings) -> dict[str, float]:
    node_count = graph.number_of_nodes()
    if node_count <= 1:
        return {node: 0.0 for node in graph.nodes()}
    try:
        if node_count <= EXACT_BETWEENNESS_LIMIT:
            return dict(nx.betweenness_centrality(graph, normalized=True, weight=None))
        return dict(
            nx.betweenness_centrality(
                graph,
                k=BETWEENNESS_SAMPLE,
                seed=int(settings.layout_seed),
                normalized=True,
                weight=None,
            )
        )
    except Exception as exc:  # 兜底：不退化为错误，图谱仍可返回
        logger.warning("betweenness 计算失败，按 0 处理: %s", exc)
        return {node: 0.0 for node in graph.nodes()}


def _clustering(graph: nx.DiGraph) -> dict[str, float]:
    if graph.number_of_nodes() <= 1:
        return {node: 0.0 for node in graph.nodes()}
    try:
        return dict(nx.clustering(graph.to_undirected()))
    except Exception as exc:
        logger.warning("聚类系数计算失败，按 0 处理: %s", exc)
        return {node: 0.0 for node in graph.nodes()}


def _assign_groups(graph: nx.DiGraph) -> dict[str, str]:
    nodes = list(graph.nodes())
    if not nodes:
        return {}
    communities: list[set[str]] = []
    undirected = graph.to_undirected()
    if undirected.number_of_edges() > 0:
        try:
            communities = [
                set(members)
                for members in nx.algorithms.community.greedy_modularity_communities(undirected, weight="weight")
            ]
        except Exception as exc:
            logger.warning("社区发现失败，回退弱连通分量: %s", exc)
            communities = []
    if not communities:
        communities = [set(members) for members in nx.weakly_connected_components(graph)]
    covered: set[str] = set().union(*communities) if communities else set()
    for node in nodes:
        if node not in covered:
            communities.append({node})
    communities.sort(key=lambda members: (min(members), -len(members)))
    return {node: f"g{index}" for index, members in enumerate(communities, start=1) for node in members}


def _group_labels(
    graph: nx.DiGraph,
    groups: Mapping[str, str],
    briefs: Mapping[str, Mapping[str, Any]],
) -> dict[str, str]:
    labels: dict[str, str] = {}
    nodes = list(graph.nodes())
    for group in sorted(set(groups.values())):
        members = [node for node in nodes if groups.get(node) == group]
        text = " ".join(
            f"{briefs[node]['title']} {briefs[node]['abstract']}" for node in members if node in briefs
        )
        keywords = [word for word in keyword_candidates(text, limit=8) if len(word) >= 3][:3]
        labels[group] = truncate(" · ".join(keywords), 60) or "未命名主题"
    return labels


def _classify_roles(
    graph: nx.DiGraph,
    pagerank: Mapping[str, float],
    indegree: Mapping[str, int],
    outdegree: Mapping[str, int],
    betweenness: Mapping[str, float],
) -> tuple[dict[str, str], list[str], list[str], list[str]]:
    """识别基石 / 桥梁 / 边缘衍生论文，返回 ``(roles, foundation, bridge, peripheral)``。"""
    nodes = list(graph.nodes())
    if not nodes:
        return {}, [], [], []
    median = float(statistics.median([pagerank.get(node, 0.0) for node in nodes])) if pagerank else 0.0
    foundation = sorted(
        [
            node
            for node in nodes
            if indegree[node] >= FOUNDATION_MIN_INDEGREE
            and pagerank.get(node, 0.0) >= median * FOUNDATION_PAGERANK_FACTOR
        ],
        key=lambda node: (-pagerank.get(node, 0.0), node),
    )
    if not foundation:
        eligible = [node for node in nodes if indegree[node] >= 1 and pagerank.get(node, 0.0) > 0]
        if eligible:
            foundation = [max(eligible, key=lambda node: (pagerank[node], indegree[node], node))]
    foundation_set = set(foundation)

    max_betweenness = max(betweenness.values()) if betweenness else 0.0
    bridge: list[str] = []
    if max_betweenness > BRIDGE_MIN_BETWEENNESS:
        bridge = sorted(
            [
                node
                for node in nodes
                if node not in foundation_set
                and betweenness.get(node, 0.0) >= max_betweenness * 0.5
                and betweenness.get(node, 0.0) > BRIDGE_MIN_BETWEENNESS
            ],
            key=lambda node: (-betweenness.get(node, 0.0), node),
        )
    bridge_set = set(bridge)

    peripheral: list[str] = []
    for node in nodes:
        if node in foundation_set or node in bridge_set:
            continue
        if indegree[node] == 0 and (outdegree[node] > 0 or pagerank.get(node, 0.0) < median):
            peripheral.append(node)
        elif indegree[node] == 0 and outdegree[node] == 0 and pagerank.get(node, 0.0) <= median:
            peripheral.append(node)
    peripheral.sort(key=lambda node: (pagerank.get(node, 0.0), node))
    peripheral_set = set(peripheral)

    roles: dict[str, str] = {}
    for node in nodes:
        if node in foundation_set:
            roles[node] = "foundation"
        elif node in bridge_set:
            roles[node] = "bridge"
        elif node in peripheral_set:
            roles[node] = "peripheral"
        else:
            roles[node] = "normal"
    return roles, foundation, bridge, peripheral


def _node_topics(
    node_id: str,
    graph: nx.DiGraph,
    groups: Mapping[str, str],
    group_labels: Mapping[str, str],
    global_topics: Sequence[str],
) -> str:
    neighbors = list(graph.predecessors(node_id)) + list(graph.successors(node_id))
    labels: list[str] = []
    for neighbor in neighbors:
        label = group_labels.get(groups.get(neighbor) or "", "")
        if label and label not in labels:
            labels.append(label)
    if not labels:
        return "、".join(global_topics[:2]) or "暂无相邻主题"
    return "、".join(labels[:3])


def _build_insights(
    graph: nx.DiGraph,
    nodes: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, Any],
    foundation: Sequence[str],
    bridge: Sequence[str],
    peripheral: Sequence[str],
    groups: Mapping[str, str],
    group_labels: Mapping[str, str],
    briefs: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    by_id = {str(node["id"]): node for node in nodes}
    corpus = " ".join(f"{brief['title']} {truncate(brief['abstract'], 300)}" for brief in briefs.values())
    global_topics = [word for word in keyword_candidates(corpus, limit=6) if len(word) >= 3]
    insights: list[str] = []

    years = sorted({int(node["year"]) for node in nodes if node.get("year")})
    span = f"{years[0]}–{years[-1]}" if years else "未知"
    insights.append(
        f"图谱覆盖 {metrics['nodes']} 篇文献、{metrics['edges']} 条引用边，密度 {metrics['density']:.3f}，"
        f"弱连通分量 {metrics['components']} 个，平均聚类系数 {metrics['avg_clustering']:.3f}，时间跨度 {span} 年。"
    )

    if foundation:
        median = float(statistics.median([node["pagerank"] for node in nodes])) if nodes else 0.0
        for node_id in foundation[:3]:
            node = by_id[node_id]
            ratio = (node["pagerank"] / median) if median > 0 else 0.0
            topics = _node_topics(node_id, graph, groups, group_labels, global_topics)
            insights.append(
                f"基石论文《{node['title']}》（{node['year'] or '年份未知'}）被引 {node['indegree']} 次、"
                f"pagerank={node['pagerank']:.3f}，为全图中位数 {median:.3f} 的 {ratio:.2f} 倍；"
                f"自身引用 {node['outdegree']} 篇文献，连接主题：{topics}。"
            )
    else:
        insights.append("未识别出基石论文：0 个节点同时满足「pagerank ≥ 全图中位数 × 1.3」且「入度 ≥ 2」。")

    if bridge:
        node = by_id[bridge[0]]
        neighbor_count = len(set(graph.predecessors(node["id"])) | set(graph.successors(node["id"])))
        topics = _node_topics(node["id"], graph, groups, group_labels, global_topics)
        insights.append(
            f"桥梁论文《{node['title']}》的 betweenness={node['betweenness']:.3f}（全图最高），"
            f"连接 {neighbor_count} 篇文献，跨越 {len(topics.split('、'))} 个主题簇：{topics}，"
            "承担从基础工作到衍生工作的中介作用。"
        )
    else:
        insights.append("未出现 betweenness 大于 0 的桥梁节点（0 个），引用关系以直接继承为主。")

    if peripheral:
        preview = "、".join(
            f"《{by_id[node_id]['title']}》（入度 {by_id[node_id]['indegree']}、"
            f"出度 {by_id[node_id]['outdegree']}、pagerank {by_id[node_id]['pagerank']:.3f}）"
            for node_id in peripheral[:3]
        )
        insights.append(f"边缘衍生论文 {len(peripheral)} 篇，尚未被本地库其他工作引用，例如 {preview}。")
    else:
        insights.append("未发现入度为 0 的边缘衍生论文（0 篇），所有工作均已被图谱内至少 1 篇文献引用。")

    if metrics["edges"] == 0:
        insights.append("图谱内匹配到的引用边为 0 条，建议补齐参考文献的 arXiv/DOI/标题信息后重新建图。")
    return insights


# ============================================================ 公开接口
def build_graph(paper_ids: Sequence[str], *, settings: Settings | None = None) -> dict[str, Any]:
    """构建局域引用图谱并持久化。

    Args:
        paper_ids: 参与建图的本地论文 id 列表（顺序不敏感，节点按 pagerank 排序输出）。
        settings: 可选配置；缺省用全局 ``get_settings()``。

    Returns:
        ``{graph_id, paper_ids, nodes, links, metrics, foundation, peripheral, insights}``。
    """
    if not paper_ids:
        raise ValidationError("paper_ids 不能为空")
    ordered = dedupe_keep_order([str(pid).strip() for pid in paper_ids if str(pid or "").strip()])
    if not ordered:
        raise ValidationError("paper_ids 中没有合法的论文 id")
    settings = settings or get_settings()

    missing = [paper_id for paper_id in ordered if repo.get_paper(paper_id) is None]
    if missing:
        raise NotFoundError(
            f"以下论文不存在：{', '.join(missing[:5])}" + ("……" if len(missing) > 5 else ""),
            detail={"missing": missing},
        )

    briefs = {paper_id: _load_brief(paper_id, settings) for paper_id in ordered}
    _match_references(ordered)
    graph = _build_digraph(ordered)

    pagerank = _pagerank(graph)
    betweenness = _betweenness(graph, settings)
    clustering = _clustering(graph)
    groups = _assign_groups(graph)
    group_labels = _group_labels(graph, groups, briefs)
    indegree = {node: int(graph.in_degree(node)) for node in graph.nodes()}
    outdegree = {node: int(graph.out_degree(node)) for node in graph.nodes()}
    roles, foundation, bridge, peripheral = _classify_roles(graph, pagerank, indegree, outdegree, betweenness)

    nodes: list[dict[str, Any]] = []
    for paper_id in ordered:
        brief = briefs[paper_id]
        group = groups.get(paper_id, "g1")
        nodes.append(
            {
                "id": paper_id,
                "title": brief["title"],
                "year": brief["year"],
                "arxiv_id": brief["arxiv_id"],
                "pagerank": round(float(pagerank.get(paper_id, 0.0)), 6),
                "indegree": indegree[paper_id],
                "outdegree": outdegree[paper_id],
                "betweenness": round(float(betweenness.get(paper_id, 0.0)), 6),
                "clustering": round(float(clustering.get(paper_id, 0.0)), 4),
                "role": roles.get(paper_id, "normal"),
                "group": group,
                "group_label": group_labels.get(group, "未命名主题"),
            }
        )
    nodes.sort(key=lambda item: (-item["pagerank"], -item["indegree"], item["id"]))

    links = [
        {
            "source": source,
            "target": target,
            "weight": round(float(data.get("weight") or 1.0), 4),
            "evidence": str(data.get("evidence") or ""),
        }
        for source, target, data in graph.edges(data=True)
    ]
    links.sort(key=lambda item: (item["source"], item["target"]))

    clustering_values = list(clustering.values())
    metrics = {
        "nodes": graph.number_of_nodes(),
        "edges": graph.number_of_edges(),
        "density": round(float(nx.density(graph)), 4),
        "components": int(nx.number_weakly_connected_components(graph)),
        "foundation_count": len(foundation),
        "peripheral_count": len(peripheral),
        "bridge_count": len(bridge),
        "avg_clustering": round(sum(clustering_values) / len(clustering_values), 4) if clustering_values else 0.0,
    }
    insights = _build_insights(
        graph, nodes, metrics, foundation, bridge, peripheral, groups, group_labels, briefs
    )

    graph_id = short_id("graph|" + "|".join(sorted(ordered)), 16)
    payload: dict[str, Any] = {
        "version": GRAPH_VERSION,
        "nodes": nodes,
        "links": links,
        "metrics": metrics,
        "foundation": foundation,
        "bridge": bridge,
        "peripheral": peripheral,
        "insights": insights,
    }
    repo.save_graph(graph_id, ordered, payload)
    return {"graph_id": graph_id, "paper_ids": ordered, **payload}


def get_graph(graph_id: str) -> dict[str, Any]:
    """读取已持久化的图谱载荷（含 graph_id / paper_ids / created_at）。"""
    graph_id = str(graph_id or "").strip()
    if not graph_id:
        raise ValidationError("graph_id 不能为空")
    row = repo.get_graph(graph_id)
    if not row:
        raise NotFoundError(f"图谱不存在：{graph_id}", detail={"graph_id": graph_id})
    payload = dict(row.get("payload") or {})
    payload["graph_id"] = row.get("id") or graph_id
    payload["paper_ids"] = list(row.get("paper_ids") or [])
    payload["created_at"] = row.get("created_at")
    payload.setdefault("nodes", [])
    payload.setdefault("links", [])
    payload.setdefault("metrics", {})
    payload.setdefault("foundation", [])
    payload.setdefault("peripheral", [])
    payload.setdefault("bridge", [])
    payload.setdefault("insights", [])
    return payload


def list_graphs(limit: int = 20) -> list[dict[str, Any]]:
    """列出最近构建的图谱（轻量摘要，不含完整 nodes/links）。"""
    limit = max(1, min(int(limit or 20), 200))
    out: list[dict[str, Any]] = []
    for row in repo.list_graphs(limit=limit):
        detail = repo.get_graph(row["id"]) or {}
        payload = detail.get("payload") or {}
        metrics = payload.get("metrics") or {}
        paper_ids = list(row.get("paper_ids") or [])
        out.append(
            {
                "id": row["id"],
                "graph_id": row["id"],
                "paper_ids": paper_ids,
                "nodes": int(metrics.get("nodes") or len(paper_ids)),
                "edges": int(metrics.get("edges") or 0),
                "foundation_count": int(metrics.get("foundation_count") or 0),
                "peripheral_count": int(metrics.get("peripheral_count") or 0),
                "created_at": row.get("created_at"),
            }
        )
    return out

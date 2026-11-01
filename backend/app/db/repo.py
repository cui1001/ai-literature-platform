"""仓储层：所有 SQL 集中于此，业务模块只与「字典化实体」交互。

约定
----
- JSON 列在读出时自动反序列化，写入时自动序列化；
- 返回值均为普通 dict / list，便于直接 JSON 响应；
- 任何写操作不隐式开事务的，单条语句依赖 SQLite 自动提交。
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from ..core.clock import utc_now_iso
from ..core.hashing import short_id
from ..core.textutil import normalize_title
from .database import execute, executemany, query, query_one, transaction

JSON_PAPER_FIELDS = ("authors", "affiliations", "keywords", "parse_quality", "quality")
JSON_DEFAULT = {"authors": [], "affiliations": [], "keywords": [], "parse_quality": {}}


def _dumps(value: Any) -> str:
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _loads(value: Any, default: Any = None) -> Any:
    if value is None or value == "":
        return default if default is not None else None
    if isinstance(value, (list, dict, int, float)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default if default is not None else None


def _decode_paper(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    row = dict(row)
    for key in ("authors", "affiliations", "keywords"):
        row[key] = _loads(row.get(key), []) or []
    row["parse_quality"] = _loads(row.get("parse_quality"), {}) or {}
    row["ocr_used"] = bool(row.get("ocr_used"))
    row["is_latest"] = bool(row.get("is_latest"))
    if row.get("title_norm") is None and row.get("title"):
        row["title_norm"] = normalize_title(row["title"])
    return row


def _decode_rows(rows: Iterable[dict[str, Any]], json_fields: Sequence[str] = ()) -> list[dict[str, Any]]:
    out = []
    for row in rows:
        item = dict(row)
        for key in json_fields:
            item[key] = _loads(item.get(key), [] if key not in ("parse_quality",) else {})
        out.append(item)
    return out


# ============================================================ 文献
PAPER_COLUMNS = (
    "id", "sha256", "filename", "file_path", "size_bytes", "title", "title_norm", "authors",
    "affiliations", "abstract", "keywords", "year", "venue", "doi", "arxiv_id", "arxiv_version",
    "pages", "ocr_used", "ocr_engine", "layout", "parse_status", "parse_quality", "version_group",
    "version_label", "is_latest", "doc_simhash", "source", "lang", "created_at", "updated_at",
)


def upsert_paper(data: dict[str, Any]) -> dict[str, Any]:
    now = utc_now_iso()
    payload = dict(data)
    payload.setdefault("created_at", now)
    payload["updated_at"] = now
    payload.setdefault("title_norm", normalize_title(payload.get("title") or ""))
    for key in ("authors", "affiliations", "keywords"):
        if key in payload and not isinstance(payload[key], str):
            payload[key] = _dumps(payload[key])
    if "parse_quality" in payload and not isinstance(payload["parse_quality"], str):
        payload["parse_quality"] = _dumps(payload["parse_quality"])
    columns = [c for c in PAPER_COLUMNS if c in payload]
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{c}=excluded.{c}" for c in columns if c not in ("id", "created_at"))
    sql = (
        f"INSERT INTO papers ({', '.join(columns)}) VALUES ({placeholders}) "
        f"ON CONFLICT(id) DO UPDATE SET {updates}"
    )
    execute(sql, [payload[c] for c in columns])
    return get_paper(payload["id"]) or {}


def update_paper(paper_id: str, **fields: Any) -> None:
    if not fields:
        return
    fields["updated_at"] = utc_now_iso()
    for key in ("authors", "affiliations", "keywords", "parse_quality"):
        if key in fields and not isinstance(fields[key], str):
            fields[key] = _dumps(fields[key])
    assignments = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE papers SET {assignments} WHERE id=?", [*fields.values(), paper_id])


def get_paper(paper_id: str) -> dict[str, Any] | None:
    return _decode_paper(query_one("SELECT * FROM papers WHERE id=?", [paper_id]))


def get_paper_by_sha(sha256: str) -> dict[str, Any] | None:
    return _decode_paper(query_one("SELECT * FROM papers WHERE sha256=?", [sha256]))


def list_papers(
    q: str | None = None,
    *,
    sort: str = "recent",
    limit: int = 200,
    offset: int = 0,
    latest_only: bool = False,
) -> list[dict[str, Any]]:
    where, params = [], []
    if q:
        where.append("(title LIKE ? OR abstract LIKE ? OR authors LIKE ? OR filename LIKE ?)")
        like = f"%{q.strip()}%"
        params.extend([like, like, like, like])
    if latest_only:
        where.append("is_latest = 1")
    order = {
        "recent": "created_at DESC, id",
        "title": "title COLLATE NOCASE ASC",
        "year": "COALESCE(year,0) DESC, title",
        "pages": "pages DESC, title",
    }.get(sort, "created_at DESC, id")
    sql = "SELECT * FROM papers"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT ? OFFSET ?"
    params.extend([int(limit), int(offset)])
    return [_decode_paper(row) for row in query(sql, params) if row]  # type: ignore[misc]


def all_papers() -> list[dict[str, Any]]:
    return list_papers(limit=10000)


def delete_paper(paper_id: str) -> bool:
    exists = query_one("SELECT id FROM papers WHERE id=?", [paper_id])
    if not exists:
        return False
    with transaction() as conn:
        conn.execute("DELETE FROM chunks WHERE paper_id=?", (paper_id,))
        conn.execute("DELETE FROM sections WHERE paper_id=?", (paper_id,))
        conn.execute("DELETE FROM refs WHERE paper_id=?", (paper_id,))
        conn.execute("DELETE FROM translations WHERE paper_id=?", (paper_id,))
        conn.execute("DELETE FROM qa_log WHERE paper_ids LIKE ?", (f"%{paper_id}%",))
        conn.execute("DELETE FROM embeddings WHERE chunk_id NOT IN (SELECT chunk_id FROM chunks)")
        conn.execute("DELETE FROM edges WHERE src=? OR dst=?", (paper_id, paper_id))
        conn.execute("DELETE FROM papers WHERE id=?", (paper_id,))
    return True


def set_latest(version_group: str, paper_id: str) -> None:
    execute("UPDATE papers SET is_latest=0 WHERE version_group=?", [version_group])
    execute("UPDATE papers SET is_latest=1 WHERE id=?", [paper_id])


def version_members(version_group: str) -> list[dict[str, Any]]:
    rows = query("SELECT * FROM papers WHERE version_group=? ORDER BY created_at", [version_group])
    return [_decode_paper(r) for r in rows]  # type: ignore[misc]


def paper_index() -> list[dict[str, Any]]:
    """轻量索引：用于参考文献匹配与图谱构建。"""
    rows = query(
        "SELECT id, title, title_norm, authors, year, doi, arxiv_id, venue, version_group, is_latest FROM papers"
    )
    out = []
    for row in rows:
        item = dict(row)
        item["authors"] = _loads(item.get("authors"), []) or []
        item["year"] = item.get("year") or 0
        out.append(item)
    return out


def paper_stats() -> dict[str, Any]:
    row = query_one(
        "SELECT COUNT(*) AS papers, COALESCE(SUM(pages),0) AS pages, "
        "COALESCE(SUM(ocr_used),0) AS ocr_papers, COALESCE(SUM(size_bytes),0) AS bytes "
        "FROM papers"
    ) or {}
    row = dict(row)
    row["chunks"] = (query_one("SELECT COUNT(*) AS n FROM chunks") or {}).get("n", 0)
    row["sections"] = (query_one("SELECT COUNT(*) AS n FROM sections") or {}).get("n", 0)
    row["references"] = (query_one("SELECT COUNT(*) AS n FROM refs") or {}).get("n", 0)
    row["latest_papers"] = (query_one("SELECT COUNT(*) AS n FROM papers WHERE is_latest=1") or {}).get("n", 0)
    return row


def library_facets() -> dict[str, Any]:
    years = query(
        "SELECT COALESCE(year, 0) AS year, COUNT(*) AS n FROM papers GROUP BY year ORDER BY year DESC"
    )
    sources = query("SELECT source, COUNT(*) AS n FROM papers GROUP BY source ORDER BY n DESC")
    return {"years": [dict(r) for r in years], "sources": [dict(r) for r in sources]}


# ============================================================ 章节 / 块
def replace_sections(paper_id: str, sections: Sequence[dict[str, Any]]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM sections WHERE paper_id=?", (paper_id,))
        rows = []
        for idx, s in enumerate(sections):
            rows.append((
                paper_id, s.get("sid") or f"s{idx}", int(s.get("level") or 1), s.get("number") or "",
                s.get("title") or "", int(s.get("page") or 1), int(s.get("end_page") or s.get("page") or 1),
                s.get("path") or "", 1 if s.get("appendix") else 0, _dumps(s.get("block_ids") or []),
                int(s.get("order_idx", idx)),
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO sections (paper_id, sid, level, number, title, page, end_page, path, "
            "appendix, block_ids, order_idx) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def get_sections(paper_id: str) -> list[dict[str, Any]]:
    rows = query("SELECT * FROM sections WHERE paper_id=? ORDER BY order_idx, sid", [paper_id])
    out = _decode_rows(rows, ("block_ids",))
    for item in out:
        item["block_ids"] = item.get("block_ids") or []
        item["appendix"] = bool(item.get("appendix"))
    return out


def replace_chunks(paper_id: str, chunks: Sequence[dict[str, Any]]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM chunks WHERE paper_id=?", (paper_id,))
        conn.executemany(
            "INSERT OR REPLACE INTO chunks (chunk_id, paper_id, sid, page, x0, y0, x1, y1, text, "
            "order_idx, token_len, kind) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    c["chunk_id"], paper_id, c.get("sid") or "", int(c.get("page") or 1),
                    float(c.get("x0", 0)), float(c.get("y0", 0)), float(c.get("x1", 0)), float(c.get("y1", 0)),
                    c.get("text") or "", int(c.get("order_idx") or 0), int(c.get("token_len") or 0),
                    c.get("kind") or "paragraph",
                )
                for c in chunks
            ],
        )


def get_chunks(paper_id: str, *, chunk_ids: Sequence[str] | None = None) -> list[dict[str, Any]]:
    if chunk_ids is not None:
        if not chunk_ids:
            return []
        marks = ",".join("?" for _ in chunk_ids)
        rows = query(f"SELECT * FROM chunks WHERE chunk_id IN ({marks})", list(chunk_ids))
        order = {cid: i for i, cid in enumerate(chunk_ids)}
        return sorted(rows, key=lambda r: order.get(r["chunk_id"], 0))
    return query("SELECT * FROM chunks WHERE paper_id=? ORDER BY order_idx", [paper_id])


def iter_chunks(paper_ids: Sequence[str]) -> list[dict[str, Any]]:
    if not paper_ids:
        return []
    marks = ",".join("?" for _ in paper_ids)
    return query(f"SELECT * FROM chunks WHERE paper_id IN ({marks}) ORDER BY paper_id, order_idx", list(paper_ids))


def count_chunks() -> int:
    return int((query_one("SELECT COUNT(*) AS n FROM chunks") or {}).get("n") or 0)


# ============================================================ 向量
def save_embeddings(rows: Sequence[tuple[str, str, int, bytes]]) -> None:
    now = utc_now_iso()
    with transaction() as conn:
        conn.executemany(
            "INSERT INTO embeddings (chunk_id, model, dim, vec, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(chunk_id) DO UPDATE SET model=excluded.model, dim=excluded.dim, "
            "vec=excluded.vec, updated_at=excluded.updated_at",
            [(cid, model, dim, payload, now) for cid, model, dim, payload in rows],
        )


def load_embeddings(model: str) -> dict[str, bytes]:
    rows = query("SELECT chunk_id, vec FROM embeddings WHERE model=?", [model])
    return {row["chunk_id"]: row["vec"] for row in rows}


def delete_embeddings(paper_id: str) -> None:
    execute(
        "DELETE FROM embeddings WHERE chunk_id IN (SELECT chunk_id FROM chunks WHERE paper_id=?)",
        [paper_id],
    )


# ============================================================ 参考文献
def replace_refs(paper_id: str, refs: Sequence[dict[str, Any]]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM refs WHERE paper_id=?", (paper_id,))
        rows = []
        for idx, ref in enumerate(refs):
            rows.append((
                paper_id, int(ref.get("ref_idx", idx + 1)), ref.get("raw") or "", ref.get("title") or "",
                _dumps(ref.get("authors") or []), ref.get("year"), ref.get("venue") or "",
                ref.get("doi") or "", ref.get("arxiv_id") or "", ref.get("url") or "",
                ref.get("matched_paper"), float(ref.get("match_score") or 0),
                float(ref.get("value_score") or 0), ref.get("value_label") or "",
                ref.get("value_reason") or "",
            ))
        conn.executemany(
            "INSERT OR REPLACE INTO refs (paper_id, ref_idx, raw, title, authors, year, venue, doi, "
            "arxiv_id, url, matched_paper, match_score, value_score, value_label, value_reason) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )


def get_refs(paper_id: str) -> list[dict[str, Any]]:
    rows = query("SELECT * FROM refs WHERE paper_id=? ORDER BY ref_idx", [paper_id])
    out = _decode_rows(rows, ("authors",))
    for item in out:
        item["authors"] = item.get("authors") or []
    return out


def update_ref(paper_id: str, ref_idx: int, **fields: Any) -> None:
    if not fields:
        return
    if "authors" in fields and not isinstance(fields["authors"], str):
        fields["authors"] = _dumps(fields["authors"])
    assignments = ", ".join(f"{k}=?" for k in fields)
    execute(f"UPDATE refs SET {assignments} WHERE paper_id=? AND ref_idx=?", [*fields.values(), paper_id, ref_idx])


def all_refs() -> list[dict[str, Any]]:
    return _decode_rows(query("SELECT * FROM refs ORDER BY paper_id, ref_idx"), ("authors",))


# ============================================================ 图边
def replace_edges(src: str, edges: Sequence[dict[str, Any]]) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM edges WHERE src=?", (src,))
        conn.executemany(
            "INSERT OR REPLACE INTO edges (src, dst, kind, weight, evidence) VALUES (?,?,?,?,?)",
            [
                (src, e["dst"], e.get("kind") or "cites", float(e.get("weight") or 1), e.get("evidence") or "")
                for e in edges
            ],
        )


def get_edges(paper_ids: Sequence[str] | None = None) -> list[dict[str, Any]]:
    if paper_ids:
        marks = ",".join("?" for _ in paper_ids)
        rows = query(
            f"SELECT * FROM edges WHERE src IN ({marks}) AND dst IN ({marks})",
            [*paper_ids, *paper_ids],
        )
    else:
        rows = query("SELECT * FROM edges")
    return rows


# ============================================================ 翻译
def put_translation(
    paper_id: str, *, lang: str, scope: str, anchor: str, src_text: str, text: str, provider: str
) -> dict[str, Any]:
    from ..core.hashing import sha1_text

    tid = short_id(f"{paper_id}|{lang}|{scope}|{anchor}|{sha1_text(src_text)}", 20)
    execute(
        "INSERT OR REPLACE INTO translations (id, paper_id, lang, scope, anchor, src_hash, src_text, text, "
        "provider, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [tid, paper_id, lang, scope, anchor, sha1_text(src_text), src_text, text, provider, utc_now_iso()],
    )
    return {"id": tid, "text": text, "cached": False}


def get_translations(paper_id: str, lang: str = "zh") -> dict[str, dict[str, Any]]:
    rows = query("SELECT * FROM translations WHERE paper_id=? AND lang=?", [paper_id, lang])
    return {
        (row.get("anchor") or ""): {
            "id": row["id"],
            "text": row["text"],
            "src_hash": row["src_hash"],
            "provider": row["provider"],
            "scope": row["scope"],
        }
        for row in rows
    }


# ============================================================ 问答日志
def add_qa(record: dict[str, Any]) -> str:
    qid = record.get("id") or short_id(f"{record.get('question')}|{utc_now_iso()}", 20)
    execute(
        "INSERT OR REPLACE INTO qa_log (id, ts, user_id, question, paper_ids, answer, citations, verified, "
        "confidence, provider, mode, trace) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            qid, record.get("ts") or utc_now_iso(), record.get("user_id") or "local",
            record.get("question") or "", _dumps(record.get("paper_ids") or []), record.get("answer") or "",
            _dumps(record.get("citations") or []), 1 if record.get("verified") else 0,
            float(record.get("confidence") or 0), record.get("provider") or "",
            record.get("mode") or "single", _dumps(record.get("trace") or {}),
        ],
    )
    return qid


def list_qa(limit: int = 30) -> list[dict[str, Any]]:
    rows = query("SELECT * FROM qa_log ORDER BY ts DESC LIMIT ?", [int(limit)])
    out = _decode_rows(rows, ("paper_ids", "citations", "trace"))
    for item in out:
        item["verified"] = bool(item.get("verified"))
    return out


# ============================================================ 发现 / 追踪
def upsert_discovery(items: Sequence[dict[str, Any]]) -> int:
    now = utc_now_iso()
    rows = []
    for item in items:
        arxiv_id = (item.get("arxiv_id") or "").strip()
        if not arxiv_id:
            continue
        rows.append((
            arxiv_id, item.get("title") or "Untitled", _dumps(item.get("authors") or []),
            item.get("abstract") or "", item.get("comments") or "", _dumps(item.get("categories") or []),
            item.get("published") or "", item.get("updated") or "", item.get("url") or "",
            item.get("github") or "", item.get("pdf_url") or "", item.get("imported_paper") or "", now,
        ))
    if not rows:
        return 0
    with transaction() as conn:
        conn.executemany(
            "INSERT INTO discovery (arxiv_id, title, authors, abstract, comments, categories, published, "
            "updated, url, github, pdf_url, imported_paper, fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(arxiv_id) DO UPDATE SET title=excluded.title, authors=excluded.authors, "
            "abstract=excluded.abstract, comments=excluded.comments, categories=excluded.categories, "
            "published=excluded.published, updated=excluded.updated, url=excluded.url, "
            "github=excluded.github, pdf_url=excluded.pdf_url, fetched_at=excluded.fetched_at",
            rows,
        )
    return len(rows)


def _decode_discovery(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["authors"] = _loads(item.get("authors"), []) or []
    item["categories"] = _loads(item.get("categories"), []) or []
    return item


def get_discovery(arxiv_id: str) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM discovery WHERE arxiv_id=?", [arxiv_id])
    return _decode_discovery(row) if row else None


def search_discovery(text: str, limit: int = 20) -> list[dict[str, Any]]:
    like = f"%{text.strip()}%"
    rows = query(
        "SELECT * FROM discovery WHERE title LIKE ? OR abstract LIKE ? OR arxiv_id LIKE ? "
        "ORDER BY published DESC LIMIT ?",
        [like, like, like, int(limit)],
    )
    return [_decode_discovery(r) for r in rows]


def list_discovery(limit: int = 50, *, sort: str = "published") -> list[dict[str, Any]]:
    order = "published DESC" if sort == "published" else "fetched_at DESC"
    rows = query(f"SELECT * FROM discovery ORDER BY {order} LIMIT ?", [int(limit)])
    return [_decode_discovery(r) for r in rows]


def set_imported_paper(arxiv_id: str, paper_id: str) -> None:
    execute("UPDATE discovery SET imported_paper=? WHERE arxiv_id=?", [paper_id, arxiv_id])


def ensure_user(user_id: str, name: str = "", interests: Sequence[str] | None = None) -> dict[str, Any]:
    row = query_one("SELECT * FROM users WHERE id=?", [user_id])
    if not row:
        execute(
            "INSERT INTO users (id, name, interests, created_at) VALUES (?,?,?,?)",
            [user_id, name or user_id, _dumps(list(interests or [])), utc_now_iso()],
        )
        row = query_one("SELECT * FROM users WHERE id=?", [user_id])
    item = dict(row or {})
    item["interests"] = _loads(item.get("interests"), []) or []
    return item


def add_feedback(user_id: str, target: str, action: str, weight: float = 1.0) -> None:
    ensure_user(user_id)
    execute(
        "INSERT OR REPLACE INTO feedback (user_id, target, action, weight, ts) VALUES (?,?,?,?,?)",
        [user_id, target, action, float(weight), utc_now_iso()],
    )


def get_feedback(user_id: str | None = None) -> list[dict[str, Any]]:
    if user_id:
        return query("SELECT * FROM feedback WHERE user_id=?", [user_id])
    return query("SELECT * FROM feedback")


def list_users() -> list[dict[str, Any]]:
    return query("SELECT * FROM users ORDER BY created_at")


def upsert_track_topic(topic: dict[str, Any]) -> dict[str, Any]:
    tid = topic.get("id") or short_id(f"topic|{topic.get('query')}", 12)
    execute(
        "INSERT INTO track_topics (id, name, query, enabled, last_run, hits, created_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET name=excluded.name, query=excluded.query, enabled=excluded.enabled",
        [
            tid, topic.get("name") or topic.get("query") or "", topic.get("query") or "",
            1 if topic.get("enabled", True) else 0, topic.get("last_run") or "",
            int(topic.get("hits") or 0), utc_now_iso(),
        ],
    )
    return query_one("SELECT * FROM track_topics WHERE id=?", [tid]) or {}


def list_track_topics() -> list[dict[str, Any]]:
    return query("SELECT * FROM track_topics ORDER BY created_at")


def delete_track_topic(topic_id: str) -> None:
    execute("DELETE FROM track_topics WHERE id=?", [topic_id])


def mark_topic_run(topic_id: str, hits: int) -> None:
    execute(
        "UPDATE track_topics SET last_run=?, hits=? WHERE id=?",
        [utc_now_iso(), int(hits), topic_id],
    )


def set_state(key: str, value: str) -> None:
    execute(
        "INSERT OR REPLACE INTO track_state (key, value, updated_at) VALUES (?,?,?)",
        [key, value, utc_now_iso()],
    )


def get_state(key: str, default: str | None = None) -> str | None:
    row = query_one("SELECT value FROM track_state WHERE key=?", [key])
    return row["value"] if row else default


# ============================================================ Agent 产物
def save_graph(graph_id: str, paper_ids: Sequence[str], payload: dict[str, Any]) -> str:
    execute(
        "INSERT OR REPLACE INTO graphs (id, paper_ids, payload, created_at) VALUES (?,?,?,?)",
        [graph_id, _dumps(list(paper_ids)), _dumps(payload), utc_now_iso()],
    )
    return graph_id


def get_graph(graph_id: str) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM graphs WHERE id=?", [graph_id])
    if not row:
        return None
    item = dict(row)
    item["paper_ids"] = _loads(item.get("paper_ids"), [])
    item["payload"] = _loads(item.get("payload"), {})
    return item


def list_graphs(limit: int = 20) -> list[dict[str, Any]]:
    rows = query("SELECT id, paper_ids, created_at FROM graphs ORDER BY created_at DESC LIMIT ?", [int(limit)])
    out = []
    for row in rows:
        item = dict(row)
        item["paper_ids"] = _loads(item.get("paper_ids"), [])
        out.append(item)
    return out


def save_survey(record: dict[str, Any]) -> str:
    sid = record.get("id") or short_id(f"survey|{record.get('graph_id')}|{record.get('title')}", 16)
    execute(
        "INSERT OR REPLACE INTO surveys (id, graph_id, paper_ids, title, markdown, sections, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            sid, record.get("graph_id") or "", _dumps(record.get("paper_ids") or []),
            record.get("title") or "领域综述", record.get("markdown") or "",
            _dumps(record.get("sections") or {}), utc_now_iso(),
        ],
    )
    return sid


def get_survey(survey_id: str) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM surveys WHERE id=?", [survey_id])
    if not row:
        return None
    item = dict(row)
    item["paper_ids"] = _loads(item.get("paper_ids"), [])
    item["sections"] = _loads(item.get("sections"), {})
    return item


def list_surveys(limit: int = 20) -> list[dict[str, Any]]:
    rows = query("SELECT id, title, graph_id, paper_ids, created_at FROM surveys ORDER BY created_at DESC LIMIT ?", [int(limit)])
    out = []
    for row in rows:
        item = dict(row)
        item["paper_ids"] = _loads(item.get("paper_ids"), [])
        out.append(item)
    return out


def save_outline(record: dict[str, Any]) -> str:
    oid = record.get("id") or short_id(f"outline|{record.get('idea')}", 16)
    execute(
        "INSERT OR REPLACE INTO outlines (id, idea, paper_ids, markdown, sections, provider, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            oid, record.get("idea") or "", _dumps(record.get("paper_ids") or []),
            record.get("markdown") or "", _dumps(record.get("sections") or {}),
            record.get("provider") or "", utc_now_iso(),
        ],
    )
    return oid


def get_outline(outline_id: str) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM outlines WHERE id=?", [outline_id])
    if not row:
        return None
    item = dict(row)
    item["paper_ids"] = _loads(item.get("paper_ids"), [])
    item["sections"] = _loads(item.get("sections"), {})
    return item


def list_outlines(limit: int = 20) -> list[dict[str, Any]]:
    rows = query("SELECT id, idea, paper_ids, provider, created_at FROM outlines ORDER BY created_at DESC LIMIT ?", [int(limit)])
    out = []
    for row in rows:
        item = dict(row)
        item["paper_ids"] = _loads(item.get("paper_ids"), [])
        out.append(item)
    return out


def save_dataset(record: dict[str, Any]) -> str:
    did = record.get("id") or short_id(f"dataset|{record.get('name')}", 12)
    execute(
        "INSERT OR REPLACE INTO datasets (id, name, columns, rows, path, size_bytes, created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        [
            did, record.get("name") or "dataset", _dumps(record.get("columns") or []),
            _dumps(record.get("rows") or []), record.get("path") or "",
            int(record.get("size_bytes") or 0), utc_now_iso(),
        ],
    )
    return did


def get_dataset(dataset_id: str) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM datasets WHERE id=?", [dataset_id])
    if not row:
        return None
    item = dict(row)
    item["columns"] = _loads(item.get("columns"), [])
    item["rows"] = _loads(item.get("rows"), [])
    return item


def list_datasets(limit: int = 50) -> list[dict[str, Any]]:
    rows = query("SELECT id, name, columns, size_bytes, created_at FROM datasets ORDER BY created_at DESC LIMIT ?", [int(limit)])
    out = []
    for row in rows:
        item = dict(row)
        item["columns"] = _loads(item.get("columns"), [])
        out.append(item)
    return out


def save_chart(record: dict[str, Any]) -> str:
    cid = record.get("id") or short_id(f"chart|{record.get('dataset_id')}|{record.get('kind')}", 12)
    execute(
        "INSERT OR REPLACE INTO charts (id, dataset_id, kind, spec, svg, caption, script, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [
            cid, record.get("dataset_id") or "", record.get("kind") or "line",
            _dumps(record.get("spec") or {}), record.get("svg") or "", record.get("caption") or "",
            record.get("script") or "", utc_now_iso(),
        ],
    )
    return cid


def get_chart(chart_id: str) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM charts WHERE id=?", [chart_id])
    if not row:
        return None
    item = dict(row)
    item["spec"] = _loads(item.get("spec"), {})
    return item


def list_charts(limit: int = 50) -> list[dict[str, Any]]:
    return query("SELECT id, dataset_id, kind, caption, created_at FROM charts ORDER BY created_at DESC LIMIT ?", [int(limit)])


# ============================================================ 作业 / 轨迹
def create_job(kind: str, payload: dict[str, Any] | None = None) -> str:
    jid = short_id(f"job|{kind}|{utc_now_iso()}|{_dumps(payload or {})}", 16)
    now = utc_now_iso()
    execute(
        "INSERT INTO jobs (id, kind, status, payload, result, error, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        [jid, kind, "pending", _dumps(payload or {}), "", "", now, now],
    )
    return jid


def update_job(job_id: str, status: str, *, result: Any = None, error: str = "") -> None:
    execute(
        "UPDATE jobs SET status=?, result=?, error=?, updated_at=? WHERE id=?",
        [status, _dumps(result) if result is not None else "", error, utc_now_iso(), job_id],
    )


def get_job(job_id: str) -> dict[str, Any] | None:
    row = query_one("SELECT * FROM jobs WHERE id=?", [job_id])
    if not row:
        return None
    item = dict(row)
    item["payload"] = _loads(item.get("payload"), {})
    item["result"] = _loads(item.get("result"), {})
    return item


def add_trace(module: str, action: str, detail: dict[str, Any] | Any) -> str:
    tid = short_id(f"trace|{module}|{action}|{_dumps(detail)}|{utc_now_iso()}", 16)
    execute(
        "INSERT INTO traces (id, ts, module, action, detail) VALUES (?,?,?,?,?)",
        [tid, utc_now_iso(), module, action, _dumps(detail)],
    )
    return tid


def list_traces(limit: int = 50, module: str | None = None) -> list[dict[str, Any]]:
    if module:
        rows = query("SELECT * FROM traces WHERE module=? ORDER BY ts DESC LIMIT ?", [module, int(limit)])
    else:
        rows = query("SELECT * FROM traces ORDER BY ts DESC LIMIT ?", [int(limit)])
    out = []
    for row in rows:
        item = dict(row)
        item["detail"] = _loads(item.get("detail"), {})
        out.append(item)
    return out


def table_counts() -> dict[str, int]:
    tables = [
        "papers", "sections", "chunks", "refs", "edges", "embeddings", "translations",
        "qa_log", "discovery", "feedback", "graphs", "surveys", "outlines", "datasets",
        "charts", "traces",
    ]
    out = {}
    for table in tables:
        row = query_one(f"SELECT COUNT(*) AS n FROM {table}")
        out[table] = int((row or {}).get("n") or 0)
    return out

"""模块五「局域引用图谱与学术综述生成」的单元测试。

不依赖网络、pytest 与额外依赖：使用标准库 unittest，LLM/嵌入均固定为离线通道。
手工构造 4 篇 ``ParsedPaper`` 写入解析缓存，并建立 A←B、A←C、B←D 的引用关系，
用于验证引用匹配、图度量与角色识别、综述四节结构以及 Future Work 的 open/solved 判定。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import reset_settings  # noqa: E402
from app.core.errors import NotFoundError, ValidationError  # noqa: E402
from app.core.hashing import sha1_text  # noqa: E402
from app.db import repo  # noqa: E402
from app.db.database import close_all, reset_database  # noqa: E402
from app.graph import (  # noqa: E402
    build_graph,
    get_graph,
    get_survey,
    list_graphs,
    list_surveys,
    survey,
)
from app.parsing.model import (  # noqa: E402
    BBox,
    PageInfo,
    ParsedPaper,
    RefEntry,
    Section,
    TextBlock,
)

PID_A, PID_B, PID_C, PID_D = "pa", "pb", "pc", "pd"

TITLE_A = "Retrieval Augmented Generation for Knowledge Intensive NLP"
TITLE_B = "Dense Passage Retrieval for Open Domain Question Answering"
TITLE_C = "Multilingual Dense Retrieval with Knowledge Graphs"
TITLE_D = "Streaming Inference for Multilingual Dense Retrieval"

ABSTRACT_A = (
    "We introduce retrieval augmented generation, coupling a parametric sequence model with a "
    "dense index of Wikipedia passages for knowledge intensive natural language tasks."
)
ABSTRACT_B = (
    "This paper studies dense passage retrieval for open domain question answering with a dual "
    "encoder trained on question passage pairs and evaluated on retrieval benchmarks."
)
ABSTRACT_C = (
    "We extend dual encoder retrieval to multilingual settings and combine dense passage indexes "
    "with knowledge graph structure for cross lingual question answering."
)
ABSTRACT_D = (
    "We present a streaming inference engine for multilingual dense retrieval that keeps the "
    "passage index warm and serves low latency answers over continuous query streams."
)

SECTIONS = ("研究脉络", "基石工作", "方法演化", "未来方向")


def _writable_temp_root() -> Path:
    """选择可写的临时目录根。

    受限沙箱 / 只读环境中系统临时目录可能不可写，此时回退到当前工作目录及其
    各级父目录（取第一个能创建并删除探测目录的位置）。
    """
    try:
        candidates = [Path(tempfile.gettempdir()), Path.cwd().resolve()]
        candidates.extend(Path.cwd().resolve().parents)
    except OSError:
        candidates = [Path.cwd()]
    probe_name = f".graph-test-probe-{os.getpid()}"
    for candidate in candidates:
        probe = candidate / probe_name
        try:
            if not candidate.is_dir():
                continue
            probe.mkdir()
            # 需要验证两层嵌套写入：部分受限环境允许建目录但拒绝写入其子目录
            # （例如以 0o700 创建的目录在沙箱下不可继续写入）。
            nested = probe / "nested"
            nested.mkdir()
            (nested / "probe.txt").write_text("ok", encoding="utf-8")
        except OSError:
            shutil.rmtree(probe, ignore_errors=True)
            continue
        shutil.rmtree(probe, ignore_errors=True)
        return candidate
    raise RuntimeError("找不到可写的临时目录")


def _ref(idx: int, title: str, *, arxiv_id: str = "", doi: str = "", year: int | None = None) -> RefEntry:
    raw = f"[{idx}] Some Authors. {title}. 2020."
    return RefEntry(
        idx=idx,
        raw=raw,
        title=title,
        authors=["Some Author", "Other Author"],
        year=year,
        venue="ACL",
        doi=doi,
        arxiv_id=arxiv_id,
        url="",
        block_id=f"ref-{idx}",
        page=1,
    )


def _refs_payload(refs: list[RefEntry]) -> list[dict]:
    return [
        {
            "ref_idx": ref.idx,
            "raw": ref.raw,
            "title": ref.title,
            "authors": ref.authors,
            "year": ref.year,
            "venue": ref.venue,
            "doi": ref.doi,
            "arxiv_id": ref.arxiv_id,
            "url": ref.url,
        }
        for ref in refs
    ]


class GraphModuleTest(unittest.TestCase):
    """图构建 + 综述生成的端到端测试。"""

    def setUp(self) -> None:
        # 系统临时目录在受限环境中可能不可写；临时数据放在探测到的可写目录下。
        # 不用 tempfile.TemporaryDirectory：它以 0o700 创建目录，在部分沙箱下
        # 会导致后续子目录不可写；这里用默认权限创建并自行清理。
        self.data_root = Path(_writable_temp_root()) / f"graph-test-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.data_root.mkdir(parents=True, exist_ok=True)
        self.settings = reset_settings(
            data_dir=str(self.data_root / "data"),
            llm_mode="offline",
            embed_mode="hashing",
            arxiv_mode="offline",
        )
        reset_database(self.settings)

        self._write_paper(
            PID_A,
            TITLE_A,
            2019,
            arxiv_id="1905.00001",
            doi="10.1000/rag",
            abstract=ABSTRACT_A,
        )
        self._write_paper(
            PID_B,
            TITLE_B,
            2021,
            arxiv_id="2011.00002",
            doi="10.1000/dpr",
            abstract=ABSTRACT_B,
            references=[_ref(1, TITLE_A, arxiv_id="arXiv:1905.00001v2", year=2019)],
        )
        self._write_paper(
            PID_C,
            TITLE_C,
            2022,
            arxiv_id="2201.00003",
            doi="10.1000/mdr",
            abstract=ABSTRACT_C,
            references=[_ref(1, TITLE_A, year=2019)],
            sections=[
                Section(
                    sid="s01",
                    number="1",
                    title="Introduction",
                    level=1,
                    path="1 Introduction",
                    page=1,
                    end_page=1,
                    block_ids=["c-b0", "c-b1"],
                ),
                Section(
                    sid="s02",
                    number="4",
                    title="Future Work",
                    level=1,
                    path="4 Future Work",
                    page=1,
                    end_page=1,
                    block_ids=["c-b2", "c-b3"],
                ),
            ],
            blocks=[
                self._block("c-b0", "heading", "1 Introduction", sid="s01"),
                self._block(
                    "c-b1",
                    "paragraph",
                    "Dense retrieval has become a standard first stage for open domain question answering.",
                    sid="s01",
                ),
                self._block("c-b2", "heading", "4 Future Work", sid="s02"),
                self._block(
                    "c-b3",
                    "paragraph",
                    "In future work, we plan to extend dense retrieval to multilingual settings.",
                    sid="s02",
                ),
            ],
        )
        self._write_paper(
            PID_D,
            TITLE_D,
            2024,
            arxiv_id="2401.00004",
            doi="10.1000/stream",
            abstract=ABSTRACT_D,
            references=[_ref(1, TITLE_B, doi="https://doi.org/10.1000/DPR", year=2021)],
            sections=[
                Section(
                    sid="s01",
                    number="5",
                    title="Conclusion",
                    level=1,
                    path="5 Conclusion",
                    page=1,
                    end_page=1,
                    block_ids=["d-b0", "d-b1"],
                ),
            ],
            blocks=[
                self._block("d-b0", "heading", "5 Conclusion", sid="s01"),
                self._block(
                    "d-b1",
                    "paragraph",
                    "We leave the study of real-time streaming inference to future work.",
                    sid="s01",
                ),
            ],
        )

    def tearDown(self) -> None:
        reset_database(self.settings)
        close_all()
        shutil.rmtree(self.data_root, ignore_errors=True)

    # ---------------------------------------------------------------- helpers
    def _block(self, block_id: str, kind: str, text: str, *, sid: str = "") -> TextBlock:
        return TextBlock(
            id=block_id,
            page=1,
            kind=kind,
            text=text,
            bbox=BBox(56, 80, 540, 96),
            order=0,
            sid=sid,
        )

    def _write_paper(
        self,
        paper_id: str,
        title: str,
        year: int,
        *,
        arxiv_id: str = "",
        doi: str = "",
        abstract: str = "",
        references: list[RefEntry] | None = None,
        sections: list[Section] | None = None,
        blocks: list[TextBlock] | None = None,
    ) -> ParsedPaper:
        refs = references or []
        paper_sections = sections or []
        paper_blocks = blocks or []
        paper = ParsedPaper(
            paper_id=paper_id,
            sha256=sha1_text(paper_id),
            filename=f"{paper_id}.pdf",
            title=title,
            authors=["Alice Zhang", "Bob Li"],
            abstract=abstract,
            year=year,
            arxiv_id=arxiv_id,
            doi=doi,
            venue="ACL",
            pages=[
                PageInfo(
                    number=1,
                    width=595,
                    height=842,
                    text_chars=sum(len(block.text) for block in paper_blocks),
                    blocks=len(paper_blocks),
                )
            ],
            blocks=paper_blocks,
            sections=paper_sections,
            references=refs,
            layout="single",
        )
        path = self.settings.parsed_dir / f"{paper_id}.json"
        path.write_text(json.dumps(paper.to_dict(), ensure_ascii=False), encoding="utf-8")
        repo.upsert_paper(
            {
                "id": paper_id,
                "sha256": sha1_text(paper_id),
                "title": title,
                "filename": f"{paper_id}.pdf",
                "abstract": abstract,
                "year": year,
                "arxiv_id": arxiv_id,
                "doi": doi,
                "venue": "ACL",
                "pages": 1,
                "parse_status": "done",
                "source": "manual",
                "lang": "en",
            }
        )
        repo.replace_refs(paper_id, _refs_payload(refs))
        return paper

    def _build(self) -> dict:
        return build_graph([PID_A, PID_B, PID_C, PID_D], settings=self.settings)

    # ------------------------------------------------------------------ 图构建
    def test_build_graph_structure_metrics_and_roles(self) -> None:
        result = self._build()

        self.assertEqual(result["metrics"]["nodes"], 4)
        self.assertGreaterEqual(result["metrics"]["edges"], 3)
        self.assertEqual(result["metrics"]["edges"], 3)
        self.assertEqual(len(result["nodes"]), 4)
        self.assertEqual(len(result["links"]), 3)

        pageranks = [node["pagerank"] for node in result["nodes"]]
        self.assertEqual(pageranks, sorted(pageranks, reverse=True))

        roles = {node["id"]: node["role"] for node in result["nodes"]}
        self.assertEqual(roles[PID_A], "foundation")
        self.assertEqual(roles[PID_B], "bridge")
        self.assertEqual(roles[PID_D], "peripheral")
        self.assertTrue(set(roles.values()) <= {"foundation", "bridge", "peripheral", "normal"})

        self.assertIn(PID_A, result["foundation"])
        self.assertIn(PID_D, result["peripheral"])
        self.assertEqual(result["metrics"]["foundation_count"], len(result["foundation"]))
        self.assertEqual(result["metrics"]["peripheral_count"], len(result["peripheral"]))
        self.assertGreaterEqual(result["metrics"]["density"], 0.0)
        self.assertGreaterEqual(result["metrics"]["components"], 1)

        nodes = {node["id"]: node for node in result["nodes"]}
        self.assertEqual(nodes[PID_A]["indegree"], 2)
        self.assertEqual(nodes[PID_A]["outdegree"], 0)
        self.assertEqual(nodes[PID_D]["indegree"], 0)
        self.assertEqual(nodes[PID_D]["outdegree"], 1)
        self.assertGreater(nodes[PID_B]["betweenness"], 0.0)
        self.assertTrue(all(node["group"] for node in result["nodes"]))
        self.assertTrue(all(node["year"] for node in result["nodes"]))

        for link in result["links"]:
            self.assertIn(link["source"], {PID_A, PID_B, PID_C, PID_D})
            self.assertIn(link["target"], {PID_A, PID_B, PID_C, PID_D})
            self.assertGreater(link["weight"], 0.0)
            self.assertTrue(link["evidence"])

        self.assertTrue(result["insights"])
        self.assertTrue(any(re.search(r"\d", item) for item in result["insights"]))
        joined = "\n".join(result["insights"])
        self.assertIn("被引", joined)
        self.assertIn("pagerank", joined.lower())

        # graph_id 对输入顺序不敏感，重复构建幂等。
        again = build_graph([PID_D, PID_C, PID_B, PID_A], settings=self.settings)
        self.assertEqual(result["graph_id"], again["graph_id"])
        self.assertEqual(result["nodes"], again["nodes"])

    def test_reference_matching_arxiv_doi_and_title(self) -> None:
        self._build()

        ref_b = repo.get_refs(PID_B)[0]
        self.assertEqual(ref_b["matched_paper"], PID_A)
        self.assertGreater(ref_b["match_score"], 0.99)

        ref_c = repo.get_refs(PID_C)[0]
        self.assertEqual(ref_c["matched_paper"], PID_A)
        self.assertGreater(ref_c["match_score"], 0.99)

        ref_d = repo.get_refs(PID_D)[0]
        self.assertEqual(ref_d["matched_paper"], PID_B)
        self.assertGreater(ref_d["match_score"], 0.99)

    def test_build_graph_validation(self) -> None:
        with self.assertRaises(ValidationError):
            build_graph([], settings=self.settings)
        with self.assertRaises(ValidationError):
            build_graph(["", "  "], settings=self.settings)
        with self.assertRaises(NotFoundError):
            build_graph([PID_A, "missing-paper"], settings=self.settings)

    def test_graph_roundtrip_and_list(self) -> None:
        built = self._build()
        loaded = get_graph(built["graph_id"])
        self.assertEqual(loaded["graph_id"], built["graph_id"])
        self.assertEqual(loaded["paper_ids"], [PID_A, PID_B, PID_C, PID_D])
        self.assertEqual(loaded["nodes"], built["nodes"])
        self.assertEqual(loaded["links"], built["links"])
        self.assertEqual(loaded["metrics"], built["metrics"])
        self.assertEqual(loaded["foundation"], built["foundation"])
        self.assertEqual(loaded["peripheral"], built["peripheral"])
        self.assertEqual(loaded["insights"], built["insights"])

        listed = list_graphs(limit=5)
        self.assertIn(built["graph_id"], [item["id"] for item in listed])
        summary = next(item for item in listed if item["id"] == built["graph_id"])
        self.assertEqual(summary["nodes"], 4)
        self.assertEqual(summary["edges"], 3)

        with self.assertRaises(NotFoundError):
            get_graph("no-such-graph")
        with self.assertRaises(ValidationError):
            get_graph("")

    # ------------------------------------------------------------------ 综述
    def test_survey_sections_citations_and_provider(self) -> None:
        result = survey([PID_A, PID_B, PID_C, PID_D], settings=self.settings)
        markdown = result["markdown"]

        self.assertTrue(markdown.startswith("# "))
        self.assertTrue(result["title"])
        self.assertIn(result["graph_id"], markdown)
        for section in SECTIONS:
            self.assertIn(f"## {section}", markdown)
            self.assertIn(section, result["sections"])
            self.assertTrue(result["sections"][section].strip())

        self.assertEqual(result["provider"], "offline")
        self.assertFalse(result["degraded"])
        self.assertEqual(result["paper_ids"], [PID_A, PID_B, PID_C, PID_D])
        self.assertEqual(len(result["papers"]), 4)

        self.assertTrue(result["citations"])
        for citation in result["citations"]:
            self.assertTrue(citation["paper_id"])
            self.assertGreaterEqual(citation["page"], 1)
            self.assertTrue(citation["section"])
            self.assertRegex(citation["marker"], r"^\[C\d+\]$")
            self.assertIn(citation["paper_id"], {PID_A, PID_B, PID_C, PID_D})

        self.assertRegex(markdown, r"\[C\d+\]")
        self.assertIn(TITLE_A, markdown)

    def test_future_directions_open_and_solved(self) -> None:
        result = survey([PID_A, PID_B, PID_C, PID_D], settings=self.settings)
        directions = result["future_directions"]

        self.assertGreaterEqual(len(directions), 2)
        statuses = {item["status"] for item in directions}
        self.assertIn("open", statuses)
        self.assertIn("solved", statuses)

        solved = [item for item in directions if item["status"] == "solved"]
        open_items = [item for item in directions if item["status"] == "open"]
        self.assertTrue(solved)
        self.assertTrue(open_items)

        self.assertEqual(solved[0]["source_paper"], PID_C)
        self.assertEqual(solved[0]["evidence"]["paper_id"], PID_D)
        self.assertIn("multilingual", solved[0]["direction"].lower())
        self.assertIn("multilingual", solved[0]["evidence"]["title"].lower())
        self.assertTrue(solved[0]["evidence"]["matched_terms"])
        self.assertGreater(solved[0]["evidence"]["score"], 0.0)

        self.assertEqual(open_items[0]["source_paper"], PID_D)
        self.assertIn("streaming", open_items[0]["direction"].lower())
        self.assertIn("checked_papers", open_items[0]["evidence"])

        self.assertEqual(result["excluded_ideas"], solved)
        self.assertIn("已被后续工作解决", result["markdown"])
        for item in directions:
            self.assertIn("source_title", item)
            self.assertTrue(item["citation"].startswith("[C"))

    def test_survey_roundtrip_save_get_list(self) -> None:
        result = survey(
            [PID_A, PID_B, PID_C, PID_D],
            settings=self.settings,
            title="检索增强与多语言稠密检索综述",
        )
        self.assertEqual(result["title"], "检索增强与多语言稠密检索综述")

        loaded = get_survey(result["survey_id"])
        self.assertEqual(loaded["survey_id"], result["survey_id"])
        self.assertEqual(loaded["title"], result["title"])
        self.assertEqual(loaded["markdown"], result["markdown"])
        self.assertEqual(loaded["sections"], result["sections"])
        self.assertEqual(loaded["future_directions"], result["future_directions"])
        self.assertEqual(loaded["excluded_ideas"], result["excluded_ideas"])
        self.assertEqual(loaded["citations"], result["citations"])
        self.assertEqual(loaded["provider"], "offline")
        self.assertFalse(loaded["degraded"])
        self.assertEqual(set(loaded["sections"]), set(SECTIONS))

        summaries = list_surveys(limit=5)
        self.assertIn(result["survey_id"], [item["id"] for item in summaries])
        summary = next(item for item in summaries if item["id"] == result["survey_id"])
        self.assertEqual(summary["graph_id"], result["graph_id"])

        with self.assertRaises(NotFoundError):
            get_survey("no-such-survey")
        with self.assertRaises(ValidationError):
            get_survey("")

    def test_survey_reuses_graph_and_intersects_scope(self) -> None:
        built = self._build()
        full = survey([], settings=self.settings, graph_id=built["graph_id"])
        self.assertEqual(full["graph_id"], built["graph_id"])
        self.assertEqual(full["paper_ids"], [PID_A, PID_B, PID_C, PID_D])

        partial = survey([PID_A, PID_B], settings=self.settings, graph_id=built["graph_id"])
        self.assertEqual(partial["paper_ids"], [PID_A, PID_B])
        self.assertEqual(partial["graph_id"], built["graph_id"])

        with self.assertRaises(ValidationError):
            survey(["unknown-paper"], settings=self.settings, graph_id=built["graph_id"])
        with self.assertRaises(ValidationError):
            survey([], settings=self.settings)


if __name__ == "__main__":
    unittest.main()

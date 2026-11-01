"""学术写作辅助 Copilot：框架 / 引用真实性 / 图形脚本 / 学术翻译 / 导出。

全部离线运行：``llm_mode="offline"``、``embed_mode="hashing"``、
``arxiv_mode="offline"``，不依赖 pytest 与任何外部渲染器。测试中的 3 篇文献
使用真实公开元数据，用于证明 References 的每个字段都来自本地库而非编造。
"""

from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
_TMP_ROOT: Path | None = None


def _temp_root() -> Path:
    """选一个真正可写的临时目录，兼容 Windows 沙箱收紧系统 Temp ACL 的情况。"""

    global _TMP_ROOT
    if _TMP_ROOT is not None:
        return _TMP_ROOT
    candidates = [
        Path(tempfile.gettempdir()) / ".writing-tmp",
        ROOT.parent / ".writing-tmp",
    ]
    last_error: OSError | None = None
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            nested = candidate / ".write-probe-dir" / "nested"
            nested.mkdir(parents=True, exist_ok=True)
            probe = nested / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
            nested.rmdir()
            (candidate / ".write-probe-dir").rmdir()
        except OSError as exc:
            last_error = exc
            continue
        _TMP_ROOT = candidate
        return candidate
    raise RuntimeError(f"找不到可写临时目录: {last_error}")


class _WritingTempDir:
    """临时数据目录：显式用默认权限创建，并自行清理。

    Python 3.14 在 Windows 上会把 ``mkdir(mode)`` 映射为目录 ACL，
    ``tempfile.TemporaryDirectory`` 固定使用 0700，在本机收紧 ACL 的沙箱里
    其子目录无法继续创建文件；因此在可写的临时根下用默认权限创建。
    """

    def __init__(self) -> None:
        self.path = _temp_root() / f"tmp{uuid.uuid4().hex[:12]}"
        self.path.mkdir(parents=True, exist_ok=True)

    @property
    def name(self) -> str:
        return str(self.path)

    def cleanup(self) -> None:
        shutil.rmtree(self.path, ignore_errors=True)


from app.core.config import reset_settings  # noqa: E402
from app.core.errors import NotFoundError, ValidationError  # noqa: E402
from app.core.hashing import sha256_text  # noqa: E402
from app.db import repo  # noqa: E402
from app.db.database import close_all, reset_database  # noqa: E402
from app.writing import (  # noqa: E402
    academic_translate,
    diagram,
    draft_outline,
    export_outline,
    get_outline,
    list_outlines,
)

REQUIRED_SECTIONS = [
    "abstract",
    "introduction",
    "related_work",
    "method",
    "experiments",
    "conclusion",
]
SECTION_HEADINGS = {
    "abstract": "Abstract",
    "introduction": "Introduction",
    "related_work": "Related Work",
    "method": "Method",
    "experiments": "Experiments",
    "conclusion": "Conclusion",
}
IDEA = (
    "Retrieval-augmented generation with self-attention for reliable "
    "scientific question answering"
)

#: 真实公开论文元数据（标题 / 作者 / 年份 / 会议 / DOI / arXiv 号均可核对）
REAL_PAPERS = [
    {
        "id": "p_attention_2017",
        "filename": "attention_is_all_you_need.pdf",
        "title": "Attention Is All You Need",
        "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar", "Jakob Uszkoreit"],
        "abstract": (
            "We propose the Transformer, a model architecture based solely on "
            "attention mechanisms, dispensing with recurrence and convolutions "
            "entirely for sequence transduction."
        ),
        "year": 2017,
        "venue": "NeurIPS",
        "doi": "10.48550/arXiv.1706.03762",
        "arxiv_id": "1706.03762",
        "parse_status": "parsed",
    },
    {
        "id": "p_bert_2019",
        "filename": "bert.pdf",
        "title": "BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding",
        "authors": ["Jacob Devlin", "Ming-Wei Chang", "Kenton Lee", "Kristina Toutanova"],
        "abstract": (
            "We introduce BERT, a bidirectional encoder pre-trained with masked "
            "language modeling and next sentence prediction for natural language "
            "understanding tasks."
        ),
        "year": 2019,
        "venue": "NAACL",
        "doi": "10.18653/v1/N19-1423",
        "arxiv_id": "1810.04805",
        "parse_status": "parsed",
    },
    {
        "id": "p_rag_2020",
        "filename": "rag.pdf",
        "title": "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks",
        "authors": ["Patrick Lewis", "Ethan Perez", "Aleksandra Piktus", "Fabio Petroni"],
        "abstract": (
            "We propose retrieval-augmented generation, combining a dense "
            "retriever with a sequence-to-sequence generator for "
            "knowledge-intensive natural language processing tasks."
        ),
        "year": 2020,
        "venue": "NeurIPS",
        "doi": "10.48550/arXiv.2005.11401",
        "arxiv_id": "2005.11401",
        "parse_status": "parsed",
    },
]

DIAGRAM_SPEC = {
    "title": "Evidence-grounded RAG pipeline",
    "nodes": [
        {"id": "query", "label": "User Query", "group": "Input"},
        {"id": "retriever", "label": "Dense Retriever", "group": "Retrieval"},
        {"id": "index", "label": "Vector Index", "group": "Retrieval"},
        {"id": "llm", "label": "Generator (LLM)", "group": "Generation"},
    ],
    "edges": [
        {"source": "query", "target": "retriever", "label": "encode"},
        {"source": "retriever", "target": "index", "label": "search", "style": "dashed"},
        {"source": "index", "target": "llm", "label": "evidence blocks"},
        {"source": "llm", "target": "query", "label": "answer", "style": "bold"},
    ],
    "clusters": [
        {"id": "retrieval", "label": "Retrieval Stage", "nodes": ["retriever", "index"]},
        {"id": "generation", "label": "Generation Stage", "nodes": ["llm"]},
    ],
}

ZH_TEXT = (
    "我们提出了一种基于自注意力机制的方法 [1]，"
    "其时间复杂度为 $O(n^2)$，常数项 $\\alpha+\\beta$ 保持不变。"
)
EN_TEXT = (
    "We propose a Transformer model [2] with self-attention for machine "
    "translation and evaluate it on a benchmark dataset."
)


def _numbers(text: str) -> set[int]:
    return {int(match) for match in re.findall(r"\[(\d{1,3})\]", text)}


class WritingCopilotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = _WritingTempDir()
        self.settings = reset_settings(
            data_dir=self.tmp.name,
            llm_mode="offline",
            embed_mode="hashing",
            arxiv_mode="offline",
        )
        self.settings.ensure_dirs()
        reset_database(self.settings)
        self.papers = []
        for paper in REAL_PAPERS:
            record = dict(paper)
            record["sha256"] = sha256_text(record["id"])
            self.papers.append(repo.upsert_paper(record))
        self.paper_ids = [paper["id"] for paper in REAL_PAPERS]

    def tearDown(self) -> None:
        try:
            reset_database(self.settings)
            close_all()
        finally:
            reset_settings()
            self.tmp.cleanup()
            if _TMP_ROOT is not None:
                try:
                    _TMP_ROOT.rmdir()
                except OSError:
                    pass

    # ---------------------------------------------------------------- outline
    def _draft(self, **kwargs):
        return draft_outline(IDEA, settings=self.settings, **kwargs)

    def test_outline_has_six_sections_and_markdown_references(self) -> None:
        result = self._draft(paper_ids=self.paper_ids)
        keys = [section["key"] for section in result["sections"]]
        self.assertEqual(keys[:6], REQUIRED_SECTIONS)
        for section in result["sections"]:
            self.assertIn(section["key"], REQUIRED_SECTIONS)
            self.assertEqual(section["heading"], SECTION_HEADINGS[section["key"]])
            self.assertTrue(section["text"].strip(), f"{section['key']} 不应为空")

        self.assertIn("[1]", result["markdown"])
        self.assertIn("## References", result["markdown"])
        for ref in result["references"]:
            self.assertIn(f"[{ref['idx']}] {ref['title']}", result["markdown"])
        self.assertEqual([ref["idx"] for ref in result["references"]], [1, 2, 3])
        self.assertEqual(result["provider"], "offline")
        self.assertFalse(result["degraded"])
        self.assertIsInstance(result["notes"], list)

    def test_outline_citations_match_real_library_entries(self) -> None:
        result = self._draft(paper_ids=self.paper_ids)
        references = {ref["idx"]: ref for ref in result["references"]}
        cited: set[int] = set()
        for section in result["sections"]:
            body_numbers = _numbers(section["text"])
            self.assertEqual(body_numbers, set(section["citations"]))
            self.assertTrue(body_numbers <= set(references), section["key"])
            cited.update(body_numbers)
        # 双向：正文引用编号 == 参考文献编号
        self.assertEqual(cited, set(references))

        by_id = {paper["id"]: paper for paper in REAL_PAPERS}
        for ref in result["references"]:
            paper = by_id[ref["paper_id"]]
            self.assertEqual(ref["title"], paper["title"])
            self.assertEqual(ref["authors"], paper["authors"])
            self.assertEqual(ref["year"], paper["year"])
            self.assertEqual(ref["venue"], paper["venue"])
            self.assertEqual(ref["doi"], paper["doi"])
            self.assertEqual(ref["arxiv_id"], paper["arxiv_id"])
            self.assertTrue(repo.get_paper(ref["paper_id"]), "引用必须能回查本地库")

        checks = {check["name"]: check for check in result["checks"]}
        for name in ("sections", "reference_check", "references_from_library", "citation_markers"):
            self.assertIn(name, checks)
        self.assertTrue(all(check["passed"] for check in result["checks"]), checks)
        self.assertTrue(checks["reference_check"]["passed"])

        stored = get_outline(result["outline_id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["id"], result["outline_id"])
        self.assertEqual(stored["paper_ids"], self.paper_ids)
        self.assertEqual(stored["markdown"], result["markdown"])
        self.assertEqual(len(stored["sections"]), len(result["sections"]))

    def test_outline_whole_library_and_unknown_id(self) -> None:
        result = self._draft()
        reference_ids = {ref["paper_id"] for ref in result["references"]}
        self.assertEqual(reference_ids, set(self.paper_ids))
        self.assertEqual(len(result["references"]), len(self.paper_ids))
        self.assertTrue(all(check["passed"] for check in result["checks"]))

        partial = self._draft(paper_ids=[self.paper_ids[1], "p_not_in_library"])
        self.assertEqual([ref["paper_id"] for ref in partial["references"]], [self.paper_ids[1]])
        self.assertTrue(any("p_not_in_library" in note for note in partial["notes"]))
        cited = set()
        for section in partial["sections"]:
            cited.update(section["citations"])
        self.assertEqual(cited, {1})
        self.assertIsNone(get_outline("p_missing_outline"))

    # ---------------------------------------------------------------- diagram
    def test_diagram_all_kinds_deterministic_and_contain_labels(self) -> None:
        extensions = {"tikz": ".tex", "graphviz": ".dot", "mermaid": ".mmd", "matplotlib": ".py"}
        for kind, extension in extensions.items():
            with self.subTest(kind=kind):
                first = diagram(DIAGRAM_SPEC, kind)
                second = diagram(DIAGRAM_SPEC, kind)
                self.assertEqual(first["kind"], kind)
                self.assertEqual(first["nodes"], 4)
                self.assertEqual(first["edges"], 4)
                self.assertEqual(first["script"], second["script"])
                self.assertEqual(
                    first["script"].encode("utf-8"), second["script"].encode("utf-8")
                )
                self.assertEqual(first["filename"], second["filename"])
                self.assertTrue(first["filename"].startswith("diagram-"))
                self.assertTrue(first["filename"].endswith(extension))
                for node in DIAGRAM_SPEC["nodes"]:
                    self.assertIn(node["label"], first["script"], kind)
                for cluster in DIAGRAM_SPEC["clusters"]:
                    self.assertIn(cluster["label"], first["script"], kind)
                self.assertIsInstance(first["notes"], list)
                self.assertTrue(first["notes"])

    def test_diagram_rejects_bad_specs(self) -> None:
        with self.assertRaises(ValidationError):
            diagram(DIAGRAM_SPEC, "plantuml")
        with self.assertRaises(ValidationError):
            diagram({"nodes": [], "edges": []})
        with self.assertRaises(ValidationError):
            diagram({"nodes": DIAGRAM_SPEC["nodes"], "edges": []})
        with self.assertRaises(ValidationError):
            diagram(
                {
                    "nodes": [{"id": "a", "label": "A"}],
                    "edges": [{"source": "a", "target": "ghost"}],
                }
            )

    # -------------------------------------------------------------- translate
    def test_academic_translate_keeps_citations_and_formulas(self) -> None:
        result = academic_translate(ZH_TEXT, settings=self.settings)
        text = result["text"]
        self.assertTrue(text.strip())
        self.assertNotEqual(text, ZH_TEXT)
        self.assertIn("[1]", text)
        self.assertIn("$O(n^2)$", text)
        self.assertIn("$\\alpha+\\beta$", text)
        self.assertNotIn("\x01", text)
        self.assertEqual(result["provider"], "offline")
        self.assertFalse(result["degraded"])
        self.assertIsInstance(result["terms"], list)
        self.assertTrue(result["terms"])
        for term in result["terms"]:
            self.assertTrue(term["zh"])
            self.assertTrue(term["en"])
        self.assertGreaterEqual(result["coverage"], 0.0)
        self.assertLessEqual(result["coverage"], 1.0)
        self.assertIsInstance(result["notes"], list)

    def test_academic_translate_en_to_zh(self) -> None:
        result = academic_translate(EN_TEXT, source="en", settings=self.settings)
        self.assertTrue(result["text"].strip())
        self.assertIn("[2]", result["text"])
        self.assertNotIn("\x01", result["text"])
        self.assertEqual(result["provider"], "offline")
        self.assertFalse(result["degraded"])

        with self.assertRaises(ValidationError):
            academic_translate("", settings=self.settings)
        with self.assertRaises(ValidationError):
            academic_translate("hello", source="fr", settings=self.settings)

    # ----------------------------------------------------------------- export
    def test_export_outline_md_and_txt(self) -> None:
        outline = self._draft(paper_ids=self.paper_ids)
        out_dir = self.settings.artifacts_dir / "exports"

        md = export_outline(outline["outline_id"], "md", settings=self.settings)
        md_path = Path(md["path"])
        self.assertTrue(md_path.exists())
        self.assertEqual(md_path.parent, out_dir)
        self.assertEqual(md["filename"], md_path.name)
        self.assertEqual(md["format"], "md")
        self.assertGreater(md["size"], 0)
        self.assertIn("[1]", md_path.read_text(encoding="utf-8"))

        txt = export_outline(outline["outline_id"], "txt", settings=self.settings)
        txt_path = Path(txt["path"])
        self.assertTrue(txt_path.exists())
        self.assertGreater(txt["size"], 0)
        self.assertEqual(txt["format"], "txt")
        self.assertTrue(txt["filename"].endswith(".txt"))
        txt_content = txt_path.read_text(encoding="utf-8")
        self.assertNotIn("## ", txt_content)
        self.assertIn("[1]", txt_content)

        with self.assertRaises(ValidationError):
            export_outline(outline["outline_id"], "pdf", settings=self.settings)
        with self.assertRaises(NotFoundError):
            export_outline("p_missing_outline", settings=self.settings)

        listed = list_outlines(10)
        self.assertIn(outline["outline_id"], [item["id"] for item in listed])
        stored = get_outline(outline["outline_id"])
        self.assertEqual(stored["idea"], IDEA)


if __name__ == "__main__":
    unittest.main()

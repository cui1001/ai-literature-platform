"""阅读器：引用双向跳转、块级翻译与四种导出的端到端测试（无网络依赖）。"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import reset_settings  # noqa: E402
from app.core.hashing import sha1_text  # noqa: E402
from app.db import repo  # noqa: E402
from app.db.database import close_all, reset_database  # noqa: E402
from app.parsing.model import (  # noqa: E402
    BBox,
    PageInfo,
    ParsedPaper,
    RefEntry,
    Section,
    TextBlock,
)
from app.reading import layout, links, service  # noqa: E402
from app.reading.export import export_paper  # noqa: E402
from app.reading.translate import translate_paper  # noqa: E402


def make_paper() -> ParsedPaper:
    paper_id = "p_read_" + sha1_text("reading-test")[:8]
    body = (
        "Retrieval augmented generation improves factual reliability [1]. "
        "The retriever uses a dual encoder and the generator is frozen [2]."
    )
    blocks = [
        TextBlock(id="p1-b0", page=1, kind="heading", text="1 Introduction", bbox=BBox(56, 80, 300, 96), sid="s01", level=1, number="1", order=0),
        TextBlock(id="p1-b1", page=1, kind="paragraph", text=body, bbox=BBox(56, 100, 540, 160), sid="s01", order=1),
        TextBlock(id="p1-b2", page=1, kind="heading", text="References", bbox=BBox(56, 200, 300, 216), sid="s02", level=1, order=2),
        TextBlock(id="p1-b3", page=1, kind="reference", text="[1] Lewis et al. Retrieval-Augmented Generation. 2020.", bbox=BBox(56, 220, 540, 240), sid="s02", order=3),
        TextBlock(id="p1-b4", page=1, kind="reference", text="[2] Karpukhin et al. Dense Passage Retrieval. 2020.", bbox=BBox(56, 240, 540, 260), sid="s02", order=4),
    ]
    sections = [
        Section(sid="s01", number="1", title="Introduction", level=1, path="1 Introduction", page=1, end_page=1, block_ids=["p1-b0", "p1-b1"]),
        Section(sid="s02", number="", title="References", level=1, path="References", page=1, end_page=1, block_ids=["p1-b2", "p1-b3", "p1-b4"]),
    ]
    return ParsedPaper(
        paper_id=paper_id,
        sha256=sha1_text(paper_id),
        filename="rag_test.pdf",
        title="RAG Test Paper",
        authors=["Patrick Lewis"],
        abstract="We study retrieval augmented generation.",
        year=2020,
        pages=[PageInfo(number=1, width=595, height=842, text_chars=len(body), blocks=5, columns=1)],
        blocks=blocks,
        sections=sections,
        references=[
            RefEntry(idx=1, raw="[1] Lewis et al. Retrieval-Augmented Generation. 2020.", title="Retrieval-Augmented Generation", authors=["Lewis"], year=2020, block_id="p1-b3", page=1),
            RefEntry(idx=2, raw="[2] Karpukhin et al. Dense Passage Retrieval. 2020.", title="Dense Passage Retrieval", authors=["Karpukhin"], year=2020, block_id="p1-b4", page=1),
        ],
        layout="single",
    )


class ReadingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = reset_settings(
            data_dir=self.tmp.name,
            llm_mode="offline",
            translate_mode="offline",  # 单测不访问外网机器翻译
            embed_mode="hashing",
            arxiv_mode="offline",
        )
        reset_database(self.settings)
        self.paper = make_paper()
        repo.upsert_paper(
            {
                "id": self.paper.paper_id,
                "sha256": self.paper.sha256,
                "title": self.paper.title,
                "filename": self.paper.filename,
                "pages": 1,
                "parse_status": "done",
            }
        )
        path = self.settings.parsed_dir / f"{self.paper.paper_id}.json"
        path.write_text(json.dumps(self.paper.to_dict(), ensure_ascii=False), encoding="utf-8")

    def tearDown(self) -> None:
        reset_database(self.settings)
        close_all()
        reset_settings()
        self.tmp.cleanup()

    def test_bidirectional_links(self) -> None:
        data = links.link_map(self.paper)
        self.assertEqual(data["stats"]["citations"], 2)
        self.assertEqual(data["stats"]["linked"], 2)
        self.assertEqual(data["stats"]["references"], 2)
        refs = {item["idx"]: item for item in data["references"]}
        self.assertEqual(refs[1]["cited_count"], 1)
        self.assertEqual(refs[1]["cited_by"][0]["block_id"], "p1-b1")
        self.assertEqual(refs[1]["cited_by"][0]["bbox"][0], 56)
        self.assertEqual(data["unresolved_citations"], [])
        self.assertEqual(data["uncited_references"], [])

    def test_document_layout_and_tree(self) -> None:
        doc = service.document(self.paper.paper_id, self.settings)
        self.assertEqual(doc["paper"]["title"], "RAG Test Paper")
        self.assertEqual(len(doc["pages"][0]["blocks"]), 5)
        self.assertEqual(doc["pages"][0]["blocks"][1]["citations"], [1, 2])
        tree = layout.heading_tree(self.paper)
        self.assertEqual(len(tree), 2)
        self.assertEqual(doc["pages"][0]["image"], f"/api/papers/{self.paper.paper_id}/pages/1.png")

    def test_translate_and_export(self) -> None:
        result = translate_paper(self.paper, self.settings, lang="zh")
        self.assertGreaterEqual(result["count"], 3)
        self.assertTrue(all(piece["text"] for piece in result["pieces"]))
        self.assertEqual(result["providers"], ["offline"])
        translations = result["by_block"]
        for fmt, suffix in (("md", ".md"), ("html", ".html"), ("bilingual", ".html"), ("pdf", ".pdf")):
            info = export_paper(self.paper, fmt, translations=translations, lang="zh", settings=self.settings)
            self.assertTrue(info.exists(), fmt)
            self.assertTrue(info.name.endswith(suffix), fmt)
        pdf = export_paper(self.paper, "pdf", translations=translations, lang="zh", settings=self.settings)
        import fitz

        doc = fitz.open(pdf)
        self.assertEqual(len(doc), 1)
        text = doc[0].get_text()
        self.assertTrue(any("\u4e00" <= ch <= "\u9fff" for ch in text), "译文 PDF 应包含中文")
        doc.close()

    def test_number_guard_blocks_rewrites(self) -> None:
        """模型改写数字/单位时必须判失败，避免把 GPT-3 译成 GPT-1。"""
        from app.reading.translate import _numbers_kept

        self.assertTrue(_numbers_kept("GPT-3 has 175 billion parameters", "GPT-3 拥有 1750 亿参数"))
        self.assertFalse(_numbers_kept("GPT-3 has 175 billion parameters", "GPT-1 拥有 175 亿参数"))
        self.assertFalse(_numbers_kept("arXiv:2005.14165v1 | NeurIPS 2020", "arXiv: 0.14165v1 | NeurIPS 1"))
        self.assertFalse(_numbers_kept("Table 1: eight model sizes", "表 0：八种模型规模"))
        self.assertTrue(_numbers_kept("28.4 BLEU on WMT 2014", "在 WMT 2014 上达到 28.4 BLEU"))


if __name__ == "__main__":
    unittest.main(verbosity=2)

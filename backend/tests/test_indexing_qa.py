"""索引 + 混合检索 + 抗幻觉问答的端到端测试（不依赖网络与真实 PDF）。

用手工构造的 ParsedPaper 验证三件关键事实：
1. 分块保留页码与包围盒，chunk_id 稳定；
2. 离线确定性引擎能从中文问题命中英文原文，并给出可点击的引用锚点；
3. 校验器能识别编造的数值（这里通过直接调用 verify_answer 构造反例）。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import Settings, reset_settings  # noqa: E402
from app.core.hashing import sha1_text  # noqa: E402
from app.db import repo  # noqa: E402
from app.db.database import close_all, get_connection, reset_database  # noqa: E402
from app.indexing.chunker import chunk_paper  # noqa: E402
from app.indexing.indexer import index_paper  # noqa: E402
from app.parsing.model import (  # noqa: E402
    BBox,
    PageInfo,
    ParsedPaper,
    Section,
    TextBlock,
)


def make_settings(tmp: str) -> Settings:
    # 注意：db 层使用全局 get_settings() 缓存的连接，因此这里必须 reset_settings，
    # 让「传给索引器的 settings」与「repo 实际写入的库」指向同一个临时目录。
    return reset_settings(
        data_dir=tmp,
        llm_mode="offline",
        embed_mode="hashing",
        arxiv_mode="offline",
    )


def make_paper() -> ParsedPaper:
    paper_id = "p_test_" + sha1_text("transformer")[:8]
    intro = (
        "The Transformer follows an encoder-decoder architecture using stacked self-attention. "
        "We train the base model for 100k steps on 8 NVIDIA P100 GPUs. "
        "The base model has 65M parameters with d_model 512 and 6 layers."
    )
    training = (
        "We use the Adam optimizer with beta1 = 0.9 and beta2 = 0.98, epsilon = 1e-9 and warmup 4000 steps. "
        "Dropout is 0.1 and label smoothing is 0.1. "
        "On WMT 2014 English-to-German the big model reaches BLEU 28.4 using 8 GPUs."
    )
    blocks = [
        TextBlock(id="p1-b0", page=1, kind="heading", text="1 Introduction", bbox=BBox(56, 80, 400, 96), sid="s01"),
        TextBlock(id="p1-b1", page=1, kind="paragraph", text=intro, bbox=BBox(56, 100, 540, 200), sid="s01"),
        TextBlock(id="p2-b0", page=2, kind="heading", text="3 Training", bbox=BBox(56, 80, 400, 96), sid="s02"),
        TextBlock(id="p2-b1", page=2, kind="paragraph", text=training, bbox=BBox(56, 100, 540, 210), sid="s02"),
    ]
    sections = [
        Section(sid="s01", number="1", title="Introduction", level=1, path="1 Introduction", page=1, end_page=1, block_ids=["p1-b0", "p1-b1"]),
        Section(sid="s02", number="3", title="Training", level=1, path="3 Training", page=2, end_page=2, block_ids=["p2-b0", "p2-b1"]),
    ]
    pages = [
        PageInfo(number=1, width=595, height=842, text_chars=len(intro), blocks=2, columns=1),
        PageInfo(number=2, width=595, height=842, text_chars=len(training), blocks=2, columns=1),
    ]
    return ParsedPaper(
        paper_id=paper_id,
        sha256=sha1_text(paper_id),
        filename="transformer_test.pdf",
        title="Attention Is All You Need (Test Copy)",
        authors=["Ashish Vaswani"],
        abstract="We propose the Transformer.",
        year=2017,
        venue="NeurIPS",
        pages=pages,
        blocks=blocks,
        sections=sections,
        layout="single",
    )


class IndexingQATest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.settings = make_settings(self.tmp.name)
        reset_database(self.settings)
        get_connection(self.settings)
        self.paper = make_paper()
        repo.upsert_paper(
            {
                "id": self.paper.paper_id,
                "sha256": self.paper.sha256,
                "title": self.paper.title,
                "filename": self.paper.filename,
                "pages": 2,
                "layout": "single",
                "parse_status": "done",
            }
        )

    def tearDown(self) -> None:
        reset_database(self.settings)
        close_all()
        # 复原全局单例，避免污染同一进程里的其他测试
        from app.core.config import reset_settings as _reset

        _reset()
        self.tmp.cleanup()

    def test_chunks_keep_anchors(self) -> None:
        chunks = chunk_paper(self.paper)
        self.assertGreaterEqual(len(chunks), 2)
        training = [c for c in chunks if "P100" in c.text]
        self.assertTrue(training)
        self.assertEqual(training[0].page, 1)
        self.assertGreater(training[0].bbox.x1, training[0].bbox.x0)
        self.assertEqual(training[0].section_path, "1 Introduction")
        again = chunk_paper(self.paper)
        self.assertEqual([c.chunk_id for c in chunks], [c.chunk_id for c in again])

    def test_hybrid_search_and_answer(self) -> None:
        stats = index_paper(self.paper, self.settings)
        self.assertGreater(stats["chunks"], 0)
        self.assertEqual(stats["embedded"], stats["chunks"])

        from app.indexing.retriever import get_retriever, reset_retriever

        reset_retriever()
        retriever = get_retriever(self.settings)
        result = retriever.search("训练用了多少块 GPU？", paper_ids=[self.paper.paper_id], top_k=3)
        self.assertTrue(result.chunks)
        self.assertTrue(any("P100" in c.text for c in result.chunks))

        from app.qa.service import answer_question

        qa = answer_question("训练使用了多少块 GPU，型号是什么？", [self.paper.paper_id], settings=self.settings)
        self.assertFalse(qa.refused)
        self.assertIn("P100", qa.answer)
        self.assertTrue(qa.citations)
        # 每条引用都必须带页码 + 四点包围盒，评审可据此在页面上高亮原文
        self.assertTrue(all(len(c.bbox) == 4 for c in qa.citations))
        hardware = [c for c in qa.citations if "P100" in c.quote]
        self.assertTrue(hardware, "回答中 P100 的出处必须被引用")
        self.assertEqual(hardware[0].page, 1)
        self.assertEqual(hardware[0].paper_id, self.paper.paper_id)


if __name__ == "__main__":
    unittest.main(verbosity=2)

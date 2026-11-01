"""学术数据可视化 Copilot 的单元测试。

运行方式（在 backend 目录下）::

    python -m unittest tests.test_dataviz -v

只依赖标准库 unittest，不依赖 pytest / pandas / matplotlib。
"""

from __future__ import annotations

import os
import re
import shutil
import statistics
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import reset_settings
from app.core.errors import NotFoundError, ValidationError
from app.db.database import close_all, reset_database
from app.db import repo
from app.dataviz import (
    CHART_KINDS,
    build_chart,
    get_chart,
    get_dataset,
    list_charts,
    list_datasets,
    matplotlib_script,
    parse_csv_bytes,
    svg_from_spec,
)

CSV_TEXT = """year,method,loss,BLEU,accuracy
2013,RNN,2.85,18.4,0.612
2014,LSTM,2.41,21.7,0.648
2015,GRU,2.18,24.3,0.671
2016,Seq2Seq,1.96,26.8,0.694
2017,Attention,1.71,29.5,0.718
2018,Transformer,1.48,32.1,0.741
2019,BERT,1.32,34.6,0.759
2020,GPT-3,1.19,36.9,0.774
2021,LoRA,1.08,38.7,0.786
2022,RLHF,0.97,40.4,0.798
2023,DPO,0.89,41.8,0.807
2024,Ours,0.82,43.1,0.815
"""

LOSS_VALUES = [
    2.85,
    2.41,
    2.18,
    1.96,
    1.71,
    1.48,
    1.32,
    1.19,
    1.08,
    0.97,
    0.89,
    0.82,
]
BLEU_VALUES = [18.4, 21.7, 24.3, 26.8, 29.5, 32.1, 34.6, 36.9, 38.7, 40.4, 41.8, 43.1]
ACCURACY_VALUES = [0.612, 0.648, 0.671, 0.694, 0.718, 0.741, 0.759, 0.774, 0.786, 0.798, 0.807, 0.815]

TREND_WORDS = (
    "increase",
    "decrease",
    "rise",
    "fall",
    "peak",
    "maximum",
    "minimum",
    "trend",
    "cross",
)


class _TempDataDir:
    """创建可被子进程继续写入的临时数据目录。

    Windows 受控环境里 ``tempfile.TemporaryDirectory`` 默认以 0o700 建目录，
    会剥离继承的沙箱 ACE，导致后续无法创建 papers/ 等子目录；这里用 0o777
    创建并自行清理，测试结束仍保证目录被删除。
    """

    def __init__(self) -> None:
        base = tempfile.gettempdir()
        for _ in range(100):
            path = os.path.join(base, f"dataviz-test-{os.getpid()}-{uuid.uuid4().hex[:8]}")
            try:
                os.mkdir(path, 0o777)
            except FileExistsError:
                continue
            self.name = path
            return
        raise RuntimeError("无法创建测试临时目录")

    def cleanup(self) -> None:
        shutil.rmtree(self.name, ignore_errors=True)


class DatavizTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _TempDataDir()
        self.settings = reset_settings(
            data_dir=self._tmp.name,
            llm_mode="offline",
            embed_mode="hashing",
            arxiv_mode="offline",
        )
        reset_database(self.settings)
        self.dataset = parse_csv_bytes(CSV_TEXT.encode("utf-8"), "metrics.csv")

    def tearDown(self) -> None:
        reset_database(self.settings)
        close_all()
        reset_settings()
        self._tmp.cleanup()

    # ------------------------------------------------------------ 解析 / 统计
    def test_parse_csv_infers_columns_and_stats(self) -> None:
        dataset = self.dataset
        self.assertTrue(dataset["id"])
        self.assertEqual(dataset["name"], "metrics.csv")
        self.assertEqual(dataset["row_count"], 12)
        self.assertEqual(len(dataset["preview"]), 10)
        self.assertEqual(dataset["preview"][0]["year"], 2013.0)
        self.assertEqual(dataset["preview"][0]["method"], "RNN")

        columns = {column["name"]: column for column in dataset["columns"]}
        self.assertEqual([column["name"] for column in dataset["columns"]],
                         ["year", "method", "loss", "BLEU", "accuracy"])
        self.assertEqual([column["index"] for column in dataset["columns"]], [0, 1, 2, 3, 4])
        self.assertEqual(columns["year"]["type"], "number")
        self.assertEqual(columns["method"]["type"], "text")
        self.assertEqual(columns["loss"]["type"], "number")
        self.assertEqual(columns["BLEU"]["type"], "number")
        self.assertEqual(columns["accuracy"]["type"], "number")
        self.assertEqual(dataset["numeric_columns"], ["year", "loss", "BLEU", "accuracy"])
        self.assertNotIn("method", dataset["stats"])

        loss = dataset["stats"]["loss"]
        self.assertAlmostEqual(loss["min"], min(LOSS_VALUES), places=6)
        self.assertAlmostEqual(loss["max"], max(LOSS_VALUES), places=6)
        self.assertAlmostEqual(loss["mean"], statistics.mean(LOSS_VALUES), places=6)
        self.assertAlmostEqual(loss["median"], statistics.median(LOSS_VALUES), places=6)
        self.assertAlmostEqual(loss["std"], statistics.pstdev(LOSS_VALUES), places=6)

        bleu = dataset["stats"]["BLEU"]
        self.assertAlmostEqual(bleu["mean"], statistics.mean(BLEU_VALUES), places=6)
        self.assertGreater(bleu["std"], 0)

    def test_dataset_roundtrip_and_listing(self) -> None:
        loaded = get_dataset(self.dataset["id"])
        self.assertEqual(loaded["id"], self.dataset["id"])
        self.assertEqual(loaded["row_count"], 12)
        self.assertEqual(loaded["stats"]["accuracy"]["max"], max(ACCURACY_VALUES))

        listing = list_datasets()
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["id"], self.dataset["id"])
        self.assertEqual(listing[0]["row_count"], 12)
        self.assertEqual(listing[0]["numeric_columns"], ["year", "loss", "BLEU", "accuracy"])
        self.assertEqual(list_datasets(limit=0), [])

        with self.assertRaises(NotFoundError):
            get_dataset("missing-dataset")

    def test_gbk_csv_is_decoded(self) -> None:
        raw = "年份,方法,损失\n2020,甲,1.2\n2021,乙,0.9\n".encode("gbk")
        dataset = parse_csv_bytes(raw, "gbk_metrics.csv")
        self.assertEqual(dataset["row_count"], 2)
        self.assertEqual(dataset["numeric_columns"], ["年份", "损失"])
        self.assertEqual(dataset["stats"]["损失"]["max"], 1.2)
        self.assertEqual(dataset["preview"][0]["方法"], "甲")

    # ------------------------------------------------------------ 五种图表
    def _chart(self, spec: dict) -> dict:
        return build_chart(self.dataset["id"], spec)

    def test_all_chart_kinds_render_svg_and_script(self) -> None:
        specs = {
            "line": {
                "kind": "line",
                "title": "Loss across years",
                "x": "year",
                "series": ["loss", "BLEU"],
            },
            "bar": {
                "kind": "bar",
                "title": "BLEU by method",
                "x": "method",
                "series": ["BLEU"],
            },
            "scatter": {
                "kind": "scatter",
                "title": "Loss versus BLEU",
                "x": "loss",
                "series": ["BLEU"],
            },
            "radar3d": {
                "kind": "radar3d",
                "title": "Model capability profile",
                "x": "method",
                "radar_axes": ["loss", "BLEU", "accuracy"],
            },
            "table": {
                "kind": "table",
                "title": "Metric table",
                "note": "all values from the uploaded CSV",
            },
        }
        self.assertEqual(set(specs), set(CHART_KINDS))
        for kind, spec in specs.items():
            with self.subTest(kind=kind):
                chart = self._chart(spec)
                self.assertEqual(chart["kind"], kind)
                self.assertEqual(chart["dataset_id"], self.dataset["id"])
                self.assertIn("<svg", chart["svg"])
                self.assertIn("</svg>", chart["svg"])
                self.assertIn("viewBox=\"0 0 720 420\"", chart["svg"])
                self.assertIn(spec["title"], chart["svg"])
                self.assertGreater(len(chart["svg"]), 800)
                self.assertIn("import pandas", chart["script"])
                self.assertIn("matplotlib", chart["script"])
                self.assertIn("savefig", chart["script"])
                self.assertIn("dataviz_chart.png", chart["script"])
                self.assertTrue(chart["caption"])
                caption = chart["caption"].lower()
                self.assertTrue(
                    any(word in caption for word in TREND_WORDS),
                    f"caption lacks trend word: {chart['caption']}",
                )
                self.assertRegex(chart["caption"], r"\d")
                self.assertIsInstance(chart["stats"], dict)
                self.assertEqual(chart["provider"], "offline")
                self.assertTrue(chart["degraded"])
                self.assertTrue(chart["id"])
                self.assertTrue(chart["created_at"])

    def test_radar3d_uses_layered_polygons(self) -> None:
        chart = self._chart(
            {
                "kind": "radar3d",
                "title": "Pseudo 3D radar",
                "x": "method",
                "radar_axes": ["loss", "BLEU", "accuracy"],
            }
        )
        self.assertIn("<polygon", chart["svg"])
        self.assertGreaterEqual(chart["svg"].count("<polygon"), 8)
        self.assertEqual(chart["spec"]["radar_axes"], ["loss", "BLEU", "accuracy"])
        self.assertGreaterEqual(len(chart["spec"]["series"]), 3)

    def test_table_chart_contains_columns_and_rows(self) -> None:
        chart = self._chart({"kind": "table", "title": "Tabular view"})
        self.assertIn("year", chart["svg"])
        self.assertIn("BLEU", chart["svg"])
        self.assertEqual(chart["spec"]["kind"], "table")
        self.assertEqual(chart["stats"]["rows"], 12)

    def test_build_chart_is_deterministic(self) -> None:
        spec = {
            "kind": "line",
            "title": "Deterministic <loss> & BLEU",
            "x": "year",
            "series": ["loss", "BLEU"],
            "note": "seed <42> & offline",
        }
        first = self._chart(spec)
        second = self._chart(spec)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["svg"], second["svg"])
        self.assertEqual(first["script"], second["script"])
        self.assertEqual(first["caption"], second["caption"])
        self.assertEqual(svg_from_spec(self.dataset, spec), first["svg"])
        self.assertEqual(matplotlib_script(self.dataset, spec), first["script"])
        self.assertEqual(svg_from_spec(self.dataset, spec), svg_from_spec(self.dataset, spec))
        self.assertIn("seed &lt;42&gt; &amp; offline", first["svg"])
        self.assertIn("Deterministic &lt;loss&gt; &amp; BLEU", first["svg"])

    def test_caption_reports_trend_and_numbers(self) -> None:
        chart = self._chart(
            {"kind": "line", "title": "Loss trend", "x": "year", "series": ["loss"]}
        )
        caption = chart["caption"].lower()
        self.assertIn("decreas", caption)
        self.assertRegex(chart["caption"], r"\d")
        self.assertIsNotNone(chart["stats"]["trend"])
        self.assertLess(chart["stats"]["trend"], 0)
        self.assertAlmostEqual(chart["stats"]["first"], 2.85, places=6)
        self.assertAlmostEqual(chart["stats"]["last"], 0.82, places=6)
        self.assertAlmostEqual(chart["stats"]["max_value"], 2.85, places=6)
        self.assertIsNotNone(chart["stats"]["max_x"])

    def test_caption_detects_crossing_series(self) -> None:
        dataset = parse_csv_bytes(
            b"year,series_a,series_b\n2020,1.0,5.0\n2021,3.0,4.0\n2022,6.0,2.0\n",
            "crossing.csv",
        )
        chart = build_chart(
            dataset["id"],
            {
                "kind": "line",
                "title": "Crossing series",
                "x": "year",
                "series": ["series_a", "series_b"],
            },
        )
        self.assertEqual(chart["stats"]["crossover"], "2022")
        self.assertIn("cross", chart["caption"].lower())

    # ------------------------------------------------------------ 持久化
    def test_chart_is_persisted_and_listed(self) -> None:
        chart = self._chart(
            {"kind": "bar", "title": "Stored chart", "x": "method", "series": ["loss"]}
        )
        loaded = get_chart(chart["id"])
        self.assertEqual(loaded["id"], chart["id"])
        self.assertEqual(loaded["dataset_id"], self.dataset["id"])
        self.assertEqual(loaded["svg"], chart["svg"])
        self.assertEqual(loaded["caption"], chart["caption"])
        self.assertEqual(loaded["spec"]["x"], "method")
        self.assertEqual(loaded["title"], "Stored chart")
        self.assertIn("loss", loaded["stats"]["series"])
        self.assertEqual(repo.get_chart(chart["id"])["id"], chart["id"])

        listing = list_charts(dataset_id=self.dataset["id"])
        ids = [item["id"] for item in listing]
        self.assertIn(chart["id"], ids)
        self.assertEqual(listing[0]["id"], chart["id"])
        self.assertEqual(listing[0]["title"], "Stored chart")
        self.assertEqual(len(list_charts(limit=1)), 1)
        self.assertEqual(list_charts(dataset_id="other-dataset"), [])

        with self.assertRaises(NotFoundError):
            get_chart("missing-chart")

    # ------------------------------------------------------------ 非法输入
    def test_invalid_specs_raise_validation_error(self) -> None:
        dataset_id = self.dataset["id"]
        invalid_specs = [
            {"kind": "pie"},
            {"kind": "line", "x": "not-a-column", "series": ["loss"]},
            {"kind": "line", "x": "year", "series": ["method"]},
            {"kind": "line", "x": "year", "series": ["not-a-column"]},
            {"kind": "scatter", "x": "method", "series": ["loss"]},
            {"kind": "radar3d", "radar_axes": ["loss", "BLEU"]},
        ]
        for spec in invalid_specs:
            with self.subTest(spec=spec):
                with self.assertRaises(ValidationError):
                    build_chart(dataset_id, spec)
        # 空 series 不是错误：自动选择数值列
        chart = build_chart(dataset_id, {"kind": "bar", "x": "year", "series": []})
        self.assertTrue(chart["spec"]["series"])

        with self.assertRaises(ValidationError):
            build_chart(dataset_id, {"kind": "line", "limit": 0})
        with self.assertRaises(ValidationError):
            build_chart(dataset_id, {"kind": "line", "bins": -1})
        with self.assertRaises(ValidationError):
            build_chart(dataset_id, {"kind": "line", "series": ["loss"], "x": "year", "limit": "abc"})
        with self.assertRaises(ValidationError):
            svg_from_spec({"columns": [], "rows": []}, {"kind": "line"})

        with self.assertRaises(NotFoundError):
            build_chart("missing-dataset", {"kind": "line"})

    def test_empty_or_header_only_csv_raises(self) -> None:
        for raw in (b"", b"\n\n", b"year,loss\n", b"year,loss\n\n"):
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError):
                    parse_csv_bytes(raw, "empty.csv")

    def test_text_only_dataset_cannot_build_cartesian_chart(self) -> None:
        dataset = parse_csv_bytes(b"name,note\nalpha,first\nbeta,second\n", "text.csv")
        with self.assertRaises(ValidationError):
            build_chart(dataset["id"], {"kind": "line", "x": "name", "series": ["note"]})
        table = build_chart(dataset["id"], {"kind": "table"})
        self.assertIn("<svg", table["svg"])
        self.assertRegex(table["caption"], r"\d")


if __name__ == "__main__":
    unittest.main(verbosity=2)

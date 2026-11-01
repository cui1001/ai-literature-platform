"""发现/追踪模块（竞赛模块四）的确定性测试。

不依赖网络：检索与导入使用临时 ``fixtures/arxiv/mirror.json`` 与本地 PDF；
仓库真实离线镜像存在时额外跑一条 smoke test，缺失时按 unittest.skipUnless 跳过。
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.clock import utc_now  # noqa: E402
from app.core.config import reset_settings  # noqa: E402
from app.core.errors import ValidationError  # noqa: E402
from app.db import repo  # noqa: E402
from app.db.database import close_all, reset_database  # noqa: E402
from app.discovery import (  # noqa: E402
    feedback,
    import_paper,
    import_references,
    recommend,
    search,
    track_add,
    track_list,
    track_remove,
    tracker_run,
)
from app.providers.arxiv import reset_arxiv_cache  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
MIRROR_PATH = ROOT / "fixtures" / "arxiv" / "mirror.json"

_TMP_ROOT: Path | None = None


def _temp_root() -> Path:
    """选一个真正可写的临时目录。

    某些 Windows 沙箱会保留系统 Temp 的目录 ACL，但工作区子目录只有
    上层 root 的沙箱 SID，导致子目录内无法再创建文件。此时回退到
    仓库根目录的上一级（沙箱工作区根）并清理测试产物。
    """

    global _TMP_ROOT
    if _TMP_ROOT is not None:
        return _TMP_ROOT
    candidates = [
        Path(tempfile.gettempdir()) / ".discovery-tmp",
        ROOT.parent / ".discovery-tmp",
    ]
    last_error: OSError | None = None
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            last_error = exc
            continue
        _TMP_ROOT = candidate
        return candidate
    raise RuntimeError(f"找不到可写临时目录: {last_error}")


def _make_temp_dir() -> Path:
    """用默认 0o777 建目录。

    ``tempfile.TemporaryDirectory``/``mkdtemp`` 在 Windows 上按 0o700 建目录，
    该 ACL 不会继承沙箱 capability SID，随后 ``Settings.ensure_dirs`` 创建
    ``papers`` 等子目录会被拒绝；因此这里手工建目录并在 tearDown 清理。
    """

    path = _temp_root() / f"tmp{uuid.uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _remove_temp_dir(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


MIRROR_ENTRIES = [
    {
        "arxiv_id": "2401.00001",
        "title": "Retrieval Augmented Generation for Code",
        "authors": ["Alice Code", "Bob Builder"],
        "abstract": "We study retrieval augmented generation and release reproducible code.",
        "categories": ["cs.CL", "cs.SE"],
        "primary_category": "cs.CL",
        "published": "2024-01-01T00:00:00Z",
        "updated": "2024-02-01T00:00:00Z",
        "url": "https://arxiv.org/abs/2401.00001",
        "pdf_url": "https://arxiv.org/pdf/2401.00001.pdf",
        "github": "https://github.com/example/rag-code",
        "source": "offline-mirror",
    },
    {
        "arxiv_id": "2401.00002",
        "title": "Retrieval Augmented Generation for Science",
        "authors": ["Carol Science"],
        "abstract": "We study retrieval augmented generation for scientific discovery.",
        "categories": ["cs.CL"],
        "primary_category": "cs.CL",
        "published": "2024-03-01T00:00:00Z",
        "updated": "2024-04-01T00:00:00Z",
        "url": "https://arxiv.org/abs/2401.00002",
        "pdf_url": "https://arxiv.org/pdf/2401.00002.pdf",
        "github": "",
        "source": "offline-mirror",
    },
]


def _write_mirror(root: Path, entries: list[dict]) -> Path:
    path = Path(root) / "fixtures" / "arxiv" / "mirror.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"corpus_version": 1, "papers": entries}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def _make_pdf(path: Path, arxiv_id: str, title: str) -> Path:
    try:
        import fitz
    except ImportError:  # pragma: no cover - 环境缺 PyMuPDF 时跳过
        raise unittest.SkipTest("PyMuPDF 不可用，跳过本地 PDF 导入用例")
    path.parent.mkdir(parents=True, exist_ok=True)
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), title, fontsize=14)
    page.insert_text((72, 100), f"arXiv:{arxiv_id}", fontsize=9)
    page.insert_text(
        (72, 130),
        "Abstract. We study retrieval augmented generation with a reproducible offline pipeline.",
        fontsize=10,
    )
    document.save(str(path))
    document.close()
    return path


def _insert_paper(paper_id: str, title: str, **extra) -> dict:
    digest = ("%s" % paper_id).encode("utf-8").hex()
    payload = {
        "id": paper_id,
        "sha256": (digest * 64)[:64],
        "filename": f"{paper_id}.pdf",
        "title": title,
        "authors": ["Test Author"],
        "abstract": extra.pop("abstract", "We study retrieval augmented generation."),
        "year": 2024,
        "parse_status": "done",
    }
    payload.update(extra)
    return repo.upsert_paper(payload)


def _candidate(
    arxiv_id: str,
    title: str,
    abstract: str,
    *,
    github: str = "",
    published: str = "2024-01-01T00:00:00Z",
) -> dict:
    return {
        "arxiv_id": arxiv_id,
        "title": title,
        "authors": ["Candidate Author"],
        "abstract": abstract,
        "categories": ["cs.CL"],
        "published": published,
        "updated": published,
        "url": f"https://arxiv.org/abs/{arxiv_id}",
        "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}.pdf",
        "github": github,
    }


class DiscoveryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = _make_temp_dir()
        self.repo_tmp: Path | None = None
        self.settings = reset_settings(
            data_dir=str(self.tmp_dir),
            llm_mode="offline",
            embed_mode="hashing",
            arxiv_mode="offline",
        )
        reset_database(self.settings)
        reset_arxiv_cache()

    def tearDown(self) -> None:
        reset_database(self.settings)
        close_all()
        reset_arxiv_cache()
        _remove_temp_dir(self.tmp_dir)
        if self.repo_tmp is not None:
            _remove_temp_dir(self.repo_tmp)
        reset_settings()
        if _TMP_ROOT is not None:
            try:
                _TMP_ROOT.rmdir()
            except OSError:
                pass

    def use_temp_repo(self, entries: list[dict] | None = None) -> Path:
        self.repo_tmp = _make_temp_dir()
        root = self.repo_tmp
        if entries is not None:
            _write_mirror(root, entries)
        self.settings = reset_settings(
            data_dir=str(self.tmp_dir),
            repo_root=root,
            llm_mode="offline",
            embed_mode="hashing",
            arxiv_mode="offline",
        )
        reset_database(self.settings)
        reset_arxiv_cache()
        return root


class LocalDiscoveryTest(DiscoveryTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.use_temp_repo()

    def test_feedback_validation(self) -> None:
        with self.assertRaises(ValidationError):
            feedback("2401.00001", "meh")
        with self.assertRaises(ValidationError):
            feedback("   ", "like")

    def test_search_without_mirror_degrades(self) -> None:
        data = search("retrieval augmented generation", settings=self.settings)
        self.assertEqual(
            {"items", "query", "provider", "degraded", "notes"},
            {"items", "query", "provider", "degraded", "notes"} & set(data),
        )
        self.assertIsInstance(data["items"], list)
        self.assertTrue(data["degraded"])
        self.assertEqual(data["provider"], "offline-mirror")
        self.assertTrue(any("镜像" in note for note in data["notes"]))

    def test_recommend_profile_and_feedback(self) -> None:
        _insert_paper(
            "p_local",
            "Retrieval Augmented Generation for Science",
            abstract="Retrieval augmented generation improves factual grounding.",
            keywords=["retrieval augmented generation", "nlp"],
        )
        repo.upsert_discovery(
            [
                _candidate(
                    "2401.00001v1",
                    "Retrieval Augmented Generation",
                    "Retrieval augmented generation improves grounding.",
                ),
                _candidate(
                    "2401.00002v1",
                    "Diffusion Models for Image Synthesis",
                    "Diffusion models generate images from noise.",
                ),
            ]
        )

        first = recommend(limit=10, settings=self.settings)
        self.assertEqual(len(first["items"]), 2)
        self.assertTrue(first["profile"]["terms"])
        self.assertTrue(any("retrieval" in topic.lower() for topic in first["profile"]["topics"]))
        self.assertTrue(all(item["reason"] for item in first["items"]))
        by_id = {item["arxiv_id"]: item for item in first["items"]}
        self.assertGreater(by_id["2401.00001v1"]["score"], by_id["2401.00002v1"]["score"])

        feedback("2401.00002", "like")
        second = recommend(limit=10, settings=self.settings)
        second_by_id = {item["arxiv_id"]: item for item in second["items"]}
        self.assertGreater(
            second_by_id["2401.00002v1"]["score"], by_id["2401.00002v1"]["score"]
        )
        self.assertTrue(second_by_id["2401.00002v1"]["liked"])
        self.assertIn("点赞", second_by_id["2401.00002v1"]["reason"])

        feedback("2401.00001", "dislike")
        third = recommend(limit=10, settings=self.settings)
        third_by_id = {item["arxiv_id"]: item for item in third["items"]}
        self.assertLess(
            third_by_id["2401.00001v1"]["score"], by_id["2401.00001v1"]["score"]
        )
        self.assertTrue(third_by_id["2401.00001v1"]["disliked"])

    def test_recommend_prefers_recent_candidate(self) -> None:
        current = utc_now().year
        repo.upsert_discovery(
            [
                _candidate(
                    "2401.00010",
                    "Recent Retrieval Paper",
                    "A recent paper about retrieval.",
                    published=f"{current}-01-01T00:00:00Z",
                ),
                _candidate(
                    "2401.00011",
                    "Old Retrieval Paper",
                    "An old paper about retrieval.",
                    published=f"{current - 10}-01-01T00:00:00Z",
                ),
            ]
        )
        data = recommend(limit=10, settings=self.settings)
        by_id = {item["arxiv_id"]: item for item in data["items"]}
        self.assertGreater(by_id["2401.00010"]["score"], by_id["2401.00011"]["score"])

    def test_hide_removes_candidate(self) -> None:
        repo.upsert_discovery(
            [_candidate("2401.00001v1", "Retrieval Augmented Generation", "RAG.")]
        )
        feedback("2401.00001", "hide")
        data = recommend(limit=10, settings=self.settings)
        self.assertEqual(data["items"], [])

    def test_track_lifecycle_and_throttle(self) -> None:
        topic = track_add(
            "retrieval augmented generation",
            category="cs.CL",
            keywords=["transformer", "attention"],
        )
        self.assertTrue(topic["id"])
        self.assertEqual(topic["category"], "cs.CL")
        self.assertEqual(topic["keywords"], ["transformer", "attention"])
        self.assertIn("cat:cs.CL", topic["query"])
        self.assertIn("kw:transformer,attention", topic["query"])

        listed = track_list()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["category"], "cs.CL")
        self.assertEqual(listed[0]["keywords"], ["transformer", "attention"])
        self.assertTrue(listed[0]["enabled"])

        track_add("retrieval augmented generation", category="cs.CL", keywords=["transformer", "attention"])
        self.assertEqual(len(track_list()), 1)

        first_run = tracker_run(settings=self.settings)
        self.assertFalse(first_run["skipped"])
        self.assertEqual(first_run["topics"], 1)
        self.assertEqual(first_run["fetched"], 0)
        self.assertEqual(first_run["new_items"], 0)

        second_run = tracker_run(settings=self.settings)
        self.assertTrue(second_run["skipped"])
        self.assertEqual(second_run["fetched"], 0)

        forced = tracker_run(settings=self.settings, force=True)
        self.assertFalse(forced["skipped"])
        self.assertEqual(forced["topics"], 1)

        removed = track_remove(topic["id"])
        self.assertEqual(removed["status"], "removed")
        self.assertEqual(track_list(), [])
        self.assertEqual(track_remove(topic["id"])["status"], "not_found")

    def test_import_paper_failures_are_returned_not_raised(self) -> None:
        failure = import_paper("Attention Is All You Need", settings=self.settings)
        self.assertEqual(failure["status"], "failed")
        self.assertIsNone(failure["paper"])
        self.assertTrue(failure["reason"])
        empty = import_paper("", settings=self.settings)
        self.assertEqual(empty["status"], "failed")
        self.assertTrue(empty["reason"])

    def test_import_references_local_match_and_missing(self) -> None:
        _insert_paper("p_source", "Source Paper", abstract="Source abstract.")
        _insert_paper(
            "p_ref",
            "Retrieval Augmented Generation",
            abstract="RAG abstract.",
            arxiv_id="2401.00001",
        )
        repo.replace_refs(
            "p_source",
            [
                {"ref_idx": 1, "title": "Retrieval Augmented Generation", "year": 2024},
                {"ref_idx": 2, "title": "A Completely Missing Reference", "year": 2020},
            ],
        )
        data = import_references("p_source", limit=5, settings=self.settings)
        self.assertEqual(data["attempted"], 2)
        self.assertEqual(data["imported"], 0)
        self.assertEqual(data["skipped"], 1)
        statuses = {item["ref_idx"]: item["status"] for item in data["items"]}
        self.assertEqual(statuses[1], "matched")
        self.assertEqual(statuses[2], "failed")
        self.assertTrue(all("reason" in item for item in data["items"]))
        self.assertTrue(all(item["reason"] for item in data["items"]))

        missing = import_references("p_missing", settings=self.settings)
        self.assertEqual(missing["attempted"], 0)
        self.assertTrue(missing["reason"])


class OfflineMirrorDiscoveryTest(DiscoveryTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.repo_root = self.use_temp_repo(MIRROR_ENTRIES)

    def test_search_structure_and_code_preference(self) -> None:
        data = search(
            "retrieval augmented generation",
            max_results=5,
            prefer_code=True,
            user_id="tester",
            settings=self.settings,
        )
        self.assertEqual(
            {"items", "query", "provider", "degraded", "notes"},
            {"items", "query", "provider", "degraded", "notes"} & set(data),
        )
        self.assertEqual(data["provider"], "offline-mirror")
        self.assertTrue(data["degraded"])
        self.assertEqual(len(data["items"]), 2)
        required = {
            "arxiv_id",
            "title",
            "abstract",
            "authors",
            "year",
            "url",
            "pdf_url",
            "github",
            "has_code",
            "relevance",
            "reason",
            "imported",
            "liked",
            "disliked",
        }
        for item in data["items"]:
            self.assertTrue(required <= set(item))
            self.assertTrue(item["reason"].strip())
            self.assertTrue(item["reason"].endswith("。"))
        self.assertTrue(data["items"][0]["has_code"])
        self.assertIn("代码", data["items"][0]["reason"])
        scores = [item["relevance"] for item in data["items"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(
            data["items"],
            sorted(data["items"], key=lambda item: (-item["relevance"], item["arxiv_id"])),
        )

    def test_search_local_library_and_feedback(self) -> None:
        _insert_paper(
            "p_local",
            "Retrieval Augmented Generation for Science",
            abstract="Retrieval augmented generation for science.",
        )
        data = search("retrieval augmented generation", settings=self.settings)
        science = next(item for item in data["items"] if item["arxiv_id"] == "2401.00002")
        self.assertTrue(science["imported"] is False)
        self.assertIn("本地库", science["reason"])

        feedback("2401.00002", "like")
        liked = search("retrieval augmented generation", settings=self.settings)
        science_liked = next(item for item in liked["items"] if item["arxiv_id"] == "2401.00002")
        self.assertTrue(science_liked["liked"])
        self.assertIn("点赞", science_liked["reason"])

    def test_import_paper_from_local_fixture(self) -> None:
        pdf = _make_pdf(
            self.repo_root / "fixtures" / "papers" / "2401.00001.pdf",
            "2401.00001",
            "Retrieval Augmented Generation for Code",
        )
        self.assertTrue(pdf.is_file())
        result = import_paper("2401.00001", settings=self.settings)
        self.assertIn(result["status"], {"imported", "replaced"})
        self.assertIsNotNone(result["paper"])
        paper_id = result["paper"]["id"]
        self.assertTrue(paper_id)
        self.assertEqual(result["entry"]["arxiv_id"], "2401.00001")
        self.assertEqual(repo.get_paper(paper_id)["id"], paper_id)
        self.assertEqual(repo.get_discovery("2401.00001")["imported_paper"], paper_id)

        duplicate = import_paper("2401.00001", settings=self.settings)
        self.assertIn(duplicate["status"], {"duplicate", "imported", "replaced"})
        self.assertEqual(duplicate["paper"]["id"], paper_id)

    def test_tracker_fetches_new_items_then_throttles(self) -> None:
        track_add("retrieval augmented generation", category="cs.CL")
        first = tracker_run(settings=self.settings)
        self.assertFalse(first["skipped"])
        self.assertEqual(first["topics"], 1)
        self.assertGreaterEqual(first["fetched"], 2)
        self.assertEqual(first["new_items"], 2)
        self.assertGreaterEqual(len(repo.list_discovery(limit=10)), 2)

        throttled = tracker_run(settings=self.settings)
        self.assertTrue(throttled["skipped"])

        forced = tracker_run(settings=self.settings, force=True)
        self.assertFalse(forced["skipped"])
        self.assertGreaterEqual(forced["fetched"], 2)
        self.assertEqual(forced["new_items"], 0)

    def test_import_references_can_use_fixture(self) -> None:
        _make_pdf(
            self.repo_root / "fixtures" / "papers" / "2401.00001.pdf",
            "2401.00001",
            "Retrieval Augmented Generation for Code",
        )
        _insert_paper("p_source", "Source Paper", abstract="Source abstract.")
        repo.replace_refs(
            "p_source",
            [{"ref_idx": 1, "title": "Retrieval Augmented Generation for Code", "year": 2024}],
        )
        data = import_references("p_source", limit=5, settings=self.settings)
        self.assertEqual(data["attempted"], 1)
        self.assertEqual(data["imported"], 1)
        item = data["items"][0]
        self.assertEqual(item["status"], "imported")
        self.assertTrue(item["paper_id"])
        self.assertTrue(repo.get_paper(item["paper_id"]))


@unittest.skipUnless(MIRROR_PATH.is_file(), f"缺少离线镜像：{MIRROR_PATH}，跳过真实镜像用例")
class RepositoryMirrorSmokeTest(DiscoveryTestBase):
    def test_repository_mirror_search(self) -> None:
        payload = json.loads(MIRROR_PATH.read_text(encoding="utf-8"))
        papers = payload.get("papers") if isinstance(payload, dict) else payload
        self.assertTrue(papers)
        entry = papers[0]
        data = search(str(entry.get("arxiv_id") or entry.get("title")), max_results=3, settings=self.settings)
        self.assertTrue(data["items"])
        self.assertEqual(data["items"][0]["arxiv_id"], entry["arxiv_id"])
        self.assertTrue(data["items"][0]["reason"].strip())


if __name__ == "__main__":
    unittest.main(verbosity=2)

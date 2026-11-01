"""环境探测脚本：OCR 引擎、向量化确定性与离线引擎冒烟。

用法：python scripts/probe_ocr.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.providers.embedding import HashingEmbedder  # noqa: E402
from app.providers.ocr import ocr_page, probe_ocr  # noqa: E402
from app.providers.offline import run_offline  # noqa: E402


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    print("== OCR probe ==")
    print(json.dumps(probe_ocr(force=True), ensure_ascii=False, indent=2))

    pdf = BACKEND.parent / "legacy" / "test_docs" / "test_doc.pdf"
    if pdf.is_file():
        import fitz

        doc = fitz.open(pdf)
        page = doc[0]
        result = ocr_page(page, dpi=200)
        print(f"page_text_chars={len(page.get_text())} ocr_chars={len(result.text)} engine={result.engine}")
        print(result.text[:300])
        print("words:", [(w.text, tuple(round(v, 1) for v in w.bbox)) for w in result.words[:8]])
        doc.close()

    print("== embedding determinism ==")
    a, b = HashingEmbedder(1024), HashingEmbedder(1024)
    text = "Attention Is All You Need：自注意力机制与多头注意力"
    va, vb = a.encode_one(text), b.encode_one(text)
    print("same=", va == vb, "dim=", len(va), "norm=", round(sum(x * x for x in va), 6))

    print("== offline engine ==")
    result = run_offline(
        "answer_question",
        {
            "question": "训练使用了多少块 GPU？",
            "evidence": [
                {
                    "id": 7,
                    "paper_id": "p1",
                    "title": "Attention Is All You Need",
                    "page": 3,
                    "section": "3.2 Training",
                    "text": "We trained on 8 NVIDIA P100 GPUs. The base model was trained for 100,000 steps.",
                }
            ],
        },
    )
    print(result.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

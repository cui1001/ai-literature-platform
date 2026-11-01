#!/usr/bin/env python
"""一键启动科研助手平台（离线可复现）。

用法::

    python scripts/run_server.py                 # 离线确定性模式
    python scripts/run_server.py --demo          # 启动前导入 fixtures 自带语料
    python scripts/run_server.py --online        # 有网络/有 Key 时启用在线模型
    python scripts/run_server.py --port 8012 --data-dir ./.data

默认行为：

* 把 ``backend`` 加入 ``sys.path``，以 ``app.main:create_app`` 启动 uvicorn；
* 未显式设置时强制离线三开关（LLM/Embedding/arXiv），保证演示无需外网；
* ``--demo`` 会先建库、导入 ``fixtures/papers/**/*.pdf``（含扫描件），再启动服务。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AgentLit 科研助手平台启动器")
    parser.add_argument("--host", default=os.environ.get("HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8000")))
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR", str(ROOT / "data")))
    parser.add_argument("--demo", action="store_true", help="启动前导入仓库自带语料")
    parser.add_argument("--online", action="store_true", help="使用在线 LLM / Embedding / arXiv")
    parser.add_argument("--reload", action="store_true", help="开发模式热重载")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    os.environ["DATA_DIR"] = str(Path(args.data_dir).expanduser().resolve())
    mode = "auto" if args.online else "offline"
    os.environ.setdefault("LLM_MODE", mode)
    os.environ.setdefault("EMBED_MODE", "auto" if args.online else "hashing")
    os.environ.setdefault("ARXIV_MODE", mode)

    sys.path.insert(0, str(BACKEND))
    from app.core.config import get_settings  # noqa: PLC0415 - 需先设置环境变量
    from app.core.logging import setup_logging  # noqa: PLC0415
    from app.db.database import get_connection, init_schema  # noqa: PLC0415

    settings = get_settings()
    settings.ensure_dirs()
    setup_logging()
    init_schema(get_connection(settings))

    if args.demo:
        from app.papers import service  # noqa: PLC0415

        # 扫描件优先导入：保证演示中出现一篇真实 OCR 论文（同名可检索版随后按重复跳过）
        files = sorted((ROOT / "fixtures" / "papers" / "scanned").glob("*.pdf")) + sorted(
            (ROOT / "fixtures" / "papers").glob("*.pdf")
        )
        imported = duplicates = failed = 0
        for path in files:
            try:
                result = service.ingest_bytes(path.read_bytes(), path.name, settings=settings, source="fixture")
                status = result.get("status")
                imported += 1 if status == "imported" else 0
                duplicates += 1 if status == "duplicate" else 0
            except Exception as exc:  # noqa: BLE001 - 单篇失败不阻断演示
                failed += 1
                print(f"[demo] {path.name} 导入失败: {exc}")
        print(f"[demo] 语料导入完成：imported={imported} duplicates={duplicates} failed={failed}")

    import uvicorn  # noqa: PLC0415 - 延迟导入，便于测试环境不装 uvicorn

    print(f"[agentlit] http://{args.host}:{args.port}  data={settings.data_dir}")
    print(f"[agentlit] llm={settings.llm_mode} embed={settings.embed_mode} arxiv={settings.arxiv_mode}")
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
        app_dir=str(BACKEND),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

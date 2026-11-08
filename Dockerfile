# AgentLit 科研助手平台 —— 单机全栈镜像（FastAPI + SQLite + 静态前端）
# 构建：docker build -t agentlit:2.0.0 .
# 运行：docker run -d -p 8000:8000 -v agentlit-data:/data --name agentlit agentlit:2.0.0
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATA_DIR=/data \
    LLM_MODE=offline \
    EMBED_MODE=hashing \
    ARXIV_MODE=offline \
    PORT=8000

WORKDIR /app

# 先装依赖，利用构建缓存
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# 复制应用、前端、语料与脚本
COPY backend /app/backend
COPY frontend /app/frontend
COPY fixtures /app/fixtures
COPY scripts /app/scripts

RUN mkdir -p /data

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3).status == 200 else 1)"

# --demo 首次启动导入 14 篇论文 + 1 份扫描件；已有数据时按 sha256 判重，不会重复解析
CMD ["python", "scripts/run_server.py", "--demo", "--host", "0.0.0.0", "--port", "8000", "--data-dir", "/data"]

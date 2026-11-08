# AgentLit 的 Hugging Face Spaces 镜像
# 用途：免费 CPU 档一键部署（无需信用卡），源码仍以 GitHub master 为唯一来源。
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    LLM_MODE=offline \
    EMBED_MODE=hashing \
    ARXIV_MODE=offline

RUN apt-get update \
 && apt-get install -y --no-install-recommends git curl \
 && rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 https://github.com/cui1001/ai-literature-platform.git /app

WORKDIR /app
RUN pip install --no-cache-dir -r backend/requirements.txt

# Spaces 以非 root 用户运行，确保数据目录可写
RUN mkdir -p /data && chmod 777 /data

EXPOSE 8000

CMD ["python", "scripts/run_server.py", "--demo", "--host", "0.0.0.0", "--port", "8000", "--data-dir", "/data"]

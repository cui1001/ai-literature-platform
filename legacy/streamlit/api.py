"""FastAPI 后端 API 客户端。

前端页面统一通过此模块调用后端，不直接散落 HTTP 代码。
集中管理：base_url、错误处理、接口调用。
"""
import requests
import streamlit as st

# 后端地址（FastAPI 默认 8000）
BASE_URL = "http://127.0.0.1:8000"


def _post(path: str, json: dict | None = None, files: dict | None = None) -> dict:
    """统一 POST 请求封装，带错误处理。"""
    try:
        if files:
            resp = requests.post(f"{BASE_URL}{path}", files=files, timeout=120)
        else:
            resp = requests.post(f"{BASE_URL}{path}", json=json, timeout=120)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error("❌ 无法连接后端，请确认 FastAPI 服务已启动（uvicorn app.main:app --reload）")
        return {}
    except requests.exceptions.Timeout:
        st.error("❌ 请求超时，请稍后重试")
        return {}
    except Exception as e:
        st.error(f"❌ 请求失败: {e}")
        return {}


def _get(path: str) -> dict:
    """统一 GET 请求封装。"""
    try:
        resp = requests.get(f"{BASE_URL}{path}", timeout=60)
        resp.raise_for_status()
        return resp.json()
    except requests.exceptions.ConnectionError:
        st.error("❌ 无法连接后端，请确认 FastAPI 服务已启动")
        return {}
    except Exception as e:
        st.error(f"❌ 请求失败: {e}")
        return {}


# ===== 对话 =====
def chat(messages: list) -> str:
    """多轮对话。"""
    data = _post("/chat", json={"messages": messages})
    return data.get("answer", "")


# ===== RAG =====
def rag_ask(question: str) -> str:
    """RAG 检索问答。"""
    data = _post("/rag", json={"question": question})
    return data.get("answer", "")


def upload_file(file) -> dict:
    """上传文件入库。"""
    return _post("/upload", files={"file": file})


def add_knowledge(texts: list) -> dict:
    """手动添加知识。"""
    return _post("/knowledge/add", json={"texts": texts})


# ===== Agent =====
def agent_ask(question: str) -> str:
    """单 Agent 问答（ReAct）。"""
    data = _post("/agent/ask", json={"question": question})
    return data.get("answer", "")


def supervisor_ask(question: str) -> str:
    """Supervisor 自主编排问答。"""
    data = _post("/agent/supervisor", json={"question": question})
    return data.get("answer", "")


# ===== 基础 =====
def hello() -> str:
    """健康检查。"""
    data = _get("/hello")
    return data.get("message", "")

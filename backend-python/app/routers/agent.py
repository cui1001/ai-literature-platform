"""Agent 相关接口：把单 Agent 和 Multi-Agent 能力暴露成 HTTP。"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.agent.agent import run_agent
from app.agent.multi_agent import analyze_topic

router = APIRouter()


class AgentAskRequest(BaseModel):
    """单 Agent 问答请求。"""
    question: str


class AnalyzeTopicRequest(BaseModel):
    """Multi-Agent 主题分析请求。"""
    topic: str


@router.post("/agent/ask")
def agent_ask(req: AgentAskRequest):
    """单 Agent 问答（ReAct 自主检索）。"""
    answer = run_agent(req.question)
    return {"answer": answer}


@router.post("/agent/analyze")
def agent_analyze(req: AnalyzeTopicRequest):
    """Multi-Agent 主题分析（检索→分析→总结）。"""
    report = analyze_topic(req.topic)
    return {"report": report}

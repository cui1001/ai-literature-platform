"""Agent 相关接口：把单 Agent 和 Supervisor 能力暴露成 HTTP。"""
from fastapi import APIRouter
from pydantic import BaseModel

from app.agent.agent import run_agent
from app.agent.supervisor import run_supervisor

router = APIRouter()


class AgentAskRequest(BaseModel):
    """Agent 问答请求。"""
    question: str


@router.post("/agent/ask")
def agent_ask(req: AgentAskRequest):
    """单 Agent 问答（ReAct 自主检索）。"""
    answer = run_agent(req.question)
    return {"answer": answer}


@router.post("/agent/supervisor")
def agent_supervisor(req: AgentAskRequest):
    """Supervisor 自主编排：主管 Agent 动态决定派哪个 Worker。"""
    answer = run_supervisor(req.question)
    return {"answer": answer}

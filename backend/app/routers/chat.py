from fastapi import APIRouter
from app.models import ChatRequest
from app.services import llm_service

router = APIRouter()

@router.get("/hello")
def hello():
    return {"message": "你好，我的第一个 AI 项目！"}

@router.get("/ask")
def ask(question: str = "用一句话介绍你自己"):
    answer = llm_service.chat([{"role": "user", "content": question}])
    return {"answer": answer}

@router.post("/chat")
def chat(req: ChatRequest):
    answer = llm_service.chat(req.messages)
    return {"answer": answer}
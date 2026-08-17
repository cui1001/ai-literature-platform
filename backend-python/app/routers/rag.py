"""RAG 相关接口：/knowledge/add、/rag。"""
from fastapi import APIRouter
from app.models import RagRequest, KnowledgeAddRequest
from app.services import rag_service

router = APIRouter()

@router.post("/knowledge/add")
def knowledge_add(req: KnowledgeAddRequest):
    total = rag_service.add_documents(req.texts)
    return {"added": len(req.texts), "total": total}

@router.post("/rag")
def rag(req: RagRequest):
    answer = rag_service.answer(req.question)
    return {"answer": answer}
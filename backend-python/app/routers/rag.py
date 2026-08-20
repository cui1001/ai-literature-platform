"""RAG 相关接口"""
from fastapi import APIRouter, UploadFile, File
from app.models import RagRequest, KnowledgeAddRequest
from app.services import rag_service, chunking_service

router = APIRouter()

@router.post("/knowledge/add")
def knowledge_add(req: KnowledgeAddRequest):
    total = rag_service.add_documents(req.texts)
    return {"added": len(req.texts), "total": total}

@router.post("/rag")
def rag(req: RagRequest):
    answer = rag_service.answer(req.question)
    return {"answer": answer}

@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    """上传文本文件（.txt/.md），自动切块后入库"""
    # 读取文件内容
    content = (await file.read()).decode("utf-8")

    # 切块
    chunks = chunking_service.chunk_text(content)

    #入库
    total = rag_service.add_documents(chunks)

    return {"filename": file.filename, "chunks": len(chunks), "total": total}
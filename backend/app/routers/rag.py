"""RAG 相关接口"""
from fastapi import APIRouter, UploadFile, File
from app.models import RagRequest, KnowledgeAddRequest
from app.services import rag_service, chunking_service, file_parser

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
    """上传文献文件（.txt / .md / .pdf / .docx），解析后自动切块入库"""
    # 读取文件二进制
    content = await file.read()

    # 解析为纯文本（按扩展名分派，见 file_parser）
    text = file_parser.parse_file(file.filename, content)

    # 切块
    chunks = chunking_service.chunk_text(text)

    # 入库
    total = rag_service.add_documents(chunks)

    return {"filename": file.filename, "chunks": len(chunks), "total": total}
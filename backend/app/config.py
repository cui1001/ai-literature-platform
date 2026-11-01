"""集中管理所有配置：密钥、URL、模型名都从这里读。"""
import os
from dotenv import load_dotenv

# 读取 .env 文件
load_dotenv()

# ===== DeepSeek 对话模型 =====
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-v4-flash")

# ===== 硅基流动 embedding 模型 =====
EMBED_API_KEY = os.getenv("SILICONFLOW_API_KEY")
EMBED_BASE_URL = os.getenv("SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1")
EMBED_MODEL = os.getenv("EMBED_MODEL", "BAAI/bge-m3")

# ===== RAG 参数 =====
TOP_K = int(os.getenv("TOP_K", "2"))   # 检索返回几段

# ===== Chroma 向量库 =====
CHROMA_PATH = os.getenv("CHROMA_PATH", "./chroma_data")
import os

from dotenv import load_dotenv
from fastapi import FastAPI
from openai import OpenAI
from fastapi import Body

# 读取.env文件里的配置
load_dotenv()

# 从环境变量读密钥——不再写死！
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
# 从环境变量读 URL，没设置就用默认值
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)

app = FastAPI()

@app.get("/hello")
def hello():
    return {"message": "你好，我的第一个 AI 项目！"}

@app.get("/ask")
def ask(question: str = "用一句话介绍你自己"):
    response = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=[{"role": "user", "content": question}],
    )
    return {"answer": response.choices[0].message.content}

@app.post("/chat")
def chat(messages: list = Body(...)):
    response = client.chat.completions.create(
        model="deepseek-v4-flash",
        messages=messages,
    )
    return {"answer": response.choices[0].message.content}

"""AgentLit 文献智能平台 - 主聊天应用。

布局参考主流 RAG 应用（NebulaRAG / rag-studio / ChatGPT）：
- 侧边栏：模式切换 + 文档上传 + 系统信息
- 主区：ChatGPT 风格的聊天流（st.chat_message）
- 底部：st.chat_input 输入框（回车发送）
"""
import sys
import os
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import api  # noqa: E402

# ===== 页面配置 =====
st.set_page_config(
    page_title="AgentLit 文献智能平台",
    page_icon="📚",
    layout="wide",
)

# ===== 侧边栏 =====
with st.sidebar:
    st.title("📚 AgentLit")

    # 模式选择（RAG / Agent / Supervisor）
    st.subheader("🤖 问答模式")
    mode = st.radio(
        "选择模式",
        options=["RAG 问答", "Agent 问答", "Supervisor 编排"],
        help="RAG：检索知识库后回答；Agent：自主决定是否检索；Supervisor：多 Worker 协作",
    )

    st.divider()

    # 文档上传
    st.subheader("📄 知识库")
    uploaded = st.file_uploader("上传文献（.txt / .md / .pdf / .docx）", type=["txt", "md", "pdf", "docx"])
    if uploaded is not None:
        with st.spinner("解析入库中..."):
            result = api.upload_file(uploaded)
        if result:
            st.success(f"已入库 {result.get('chunks')} 段，共 {result.get('total')} 条")

    st.divider()
    st.caption("提示：上传文献后，RAG/Agent 才能基于资料回答")

# ===== 主聊天区 =====
st.title("📚 AgentLit 文献智能平台")

# 初始化会话消息（存储 (role, content) 列表）
if "messages" not in st.session_state:
    st.session_state.messages = []

# 展示历史消息（ChatGPT 风格气泡）
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

# 底部输入框（回车发送）
if prompt := st.chat_input("输入你的问题，回车发送..."):
    # 追加用户消息并展示
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    # 按模式调用对应接口
    with st.chat_message("assistant"):
        with st.spinner("思考中..."):
            if mode == "RAG 问答":
                answer = api.rag_ask(prompt)
            elif mode == "Agent 问答":
                answer = api.agent_ask(prompt)
            else:
                answer = api.supervisor_ask(prompt)
        st.markdown(answer if answer else "（无返回，请检查后端）")

    # 追加助手消息
    st.session_state.messages.append({"role": "assistant", "content": answer})

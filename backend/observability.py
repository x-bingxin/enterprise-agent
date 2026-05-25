"""
使用 LangChain Callback 集成 LangFuse
"""
import os
from langfuse.callback import CallbackHandler
from langfuse import Langfuse

# 创建 LangFuse callback handler
langfuse_handler = CallbackHandler(
    public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
    secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
    host=os.getenv("LANGFUSE_HOST"),
)

# 在 ChatOpenAI 中配置
# llm = ChatOpenAI(
#     model="gpt-4o",
#     callbacks=[langfuse_handler],  # ✅ LangChain 原生 callback
# )

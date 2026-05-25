"""
FastAPI 服务入口 - 企业审批 Agent
使用 LangGraph + astream_events 推送状态
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from graph.builder import build_enterprise_agent
from routes.chat import router as chat_router
from routes.admin_ws import router as admin_ws_router
from routes.system import router as system_router

load_dotenv()

# ========== 初始化 LLM ==========
llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "gpt-4o"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),
    temperature=0,
)


# ========== 应用生命周期 ==========
@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = "db/checkpoints.db"
    async with AsyncSqliteSaver.from_conn_string(db_path) as checkpointer:
        app.state.agent_graph = build_enterprise_agent(llm, checkpointer)
        yield


# ========== 创建应用 & 注册路由 ==========
app = FastAPI(title="企业审批 Agent", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(admin_ws_router)
app.include_router(system_router)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

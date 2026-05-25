# backend/main.py (完全修正版)
"""
FastAPI 服务 - 企业审批 Agent
使用 LangChain 生态，通过 astream_events 推送状态
"""
import time

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
import asyncio
import json
import os
from typing import Optional
from dotenv import load_dotenv

from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage

from contextlib import asynccontextmanager
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from state_tracker import GlobalStateTracker
from graph.builder import build_enterprise_agent

load_dotenv()


# ========== 初始化 LLM 和 Agent ==========
llm = ChatOpenAI(
    model=os.getenv("LLM_MODEL", "gpt-4o"),
    api_key=os.getenv("LLM_API_KEY"),
    base_url=os.getenv("LLM_BASE_URL", None),  # 用于 Ollama 等
    temperature=0,
)

# ================= 定义 FastAPI Lifespan =================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    管理应用生命周期：
    - yield 之前的代码在应用启动时执行
    - yield 之后的代码在应用关闭时执行
    """
    db_path = "db/checkpoints.db"
    
    # 启动时：打开异步 SQLite 连接
    async with AsyncSqliteSaver.from_conn_string(db_path) as checkpointer:
        # 编译 graph 并将其存储在 app.state 中，供所有路由共享
        app.agent_graph = build_enterprise_agent(llm, checkpointer)
        
        yield  # <-- 应用在这里运行，处理请求
        
    # 关闭时：退出 async with 块，自动安全关闭 SQLite 连接


app = FastAPI(title="企业审批 Agent", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ========== 请求模型 ==========
class ChatRequest(BaseModel):
    message: str
    user_id: str = "anonymous"
    session_id: str = "default"


class ApprovalRequest(BaseModel):
    session_id: str
    decision: str  # "approved" or "rejected"
    comment: str = ""


tracker = GlobalStateTracker()

# ========== WebSocket: /ws/admin-dashboard（管理员全局监控） ==========
@app.websocket("/ws/admin-dashboard")
async def admin_dashboard_websocket(ws: WebSocket):
    """
    管理员 Dashboard WebSocket
    
    功能：
    - 接收全局会话状态实时更新
    - 接收新的待审批工单通知
    - 支持手动刷新
    """
    await ws.accept()
    tracker.register_admin(ws)
    
    # 发送初始数据
    await ws.send_json({
        "type": "dashboard_init",
        "data": tracker.get_dashboard_data(),
    })
    
    # 心跳任务：每5秒推送一次全局状态
    async def heartbeat():
        while True:
            try:
                await asyncio.sleep(5)
                await ws.send_json({
                    "type": "heartbeat",
                    "data": tracker.get_dashboard_data(),
                    "timestamp": time.time(),
                })
            except Exception:
                break
    
    heartbeat_task = asyncio.create_task(heartbeat())
    
    try:
        while True:
            data = await ws.receive_text()
            msg = json.loads(data)
            
            if msg.get("type") == "refresh_dashboard":
                # 管理员手动刷新
                await ws.send_json({
                    "type": "dashboard_update",
                    "data": tracker.get_dashboard_data(),
                })
            
            elif msg.get("type") == "get_session_detail":
                # 获取某个会话的详细状态
                sid = msg.get("session_id")
                if sid:
                    config = {"configurable": {"thread_id": sid}}
                    state = await app.agent_graph.aget_state(config)
                    await ws.send_json({
                        "type": "session_detail",
                        "session_id": sid,
                        "values": state.values if state.values else {},
                        "is_interrupted": state.next != () and len(state.next) > 0 if state.next else False,
                    })
    
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[Admin WS] Error: {e}")
    finally:
        heartbeat_task.cancel()
        tracker.unregister_admin(ws)


# ========== 管理员 WebSocket 管理器（附加在 tracker 上） ==========
class AdminConnectionManager:
    """管理员 Dashboard 的 WebSocket 连接管理"""
    
    @staticmethod
    async def connect(ws: WebSocket):
        await ws.accept()
        tracker.admin_connections.add(ws)
        # 发送初始数据
        await ws.send_json({
            "type": "dashboard_init",
            "data": tracker.get_dashboard_data()
        })
    
    @staticmethod
    def disconnect(ws: WebSocket):
        tracker.admin_connections.discard(ws)
    
    @staticmethod
    async def handle_message(ws: WebSocket, data: dict):
        """处理管理员发来的消息"""
        msg_type = data.get("type")
        
        if msg_type == "approval_decision":
            # 管理员做出审批决策
            session_id = data.get("session_id")
            decision = data.get("decision")
            comment = data.get("comment", "")
            
            if session_id:
                # 更新审批状态
                tracker.remove_pending_approval(session_id)
                
                # 通知 Dashboard 更新
                await tracker.broadcast_to_admins({
                    "type": "approval_resolved",
                    "session_id": session_id,
                    "decision": decision,
                })
                
                # 审批决策会通过 /ws/{session_id} 的 handle 来恢复 Agent
                # 这里通知管理员，具体 Agent 恢复由单独的审批接口处理
        
        elif msg_type == "refresh_dashboard":
            # 管理员请求刷新 Dashboard
            await ws.send_json({
                "type": "dashboard_update",
                "data": tracker.get_dashboard_data()
            })

# ========== REST API ==========

@app.post("/chat")
async def chat(req: ChatRequest):
    """同步对话"""
    config = {"configurable": {"thread_id": req.session_id}}
    result = await app.agent_graph.ainvoke(
        {
            "messages": [HumanMessage(content=req.message)],
            "user_id": req.user_id,
            "session_id": req.session_id,
        },
        config,
    )

    return {
        "response": result.get("final_response", "处理完成"),
        "approval_status": result.get("approval_status"),
        "category": result.get("category"),
    }


# ========== SSE 事件格式化工具 ==========
def sse_event(event_type: str, data: dict) -> str:
    """格式化为 SSE 事件"""
    payload = json.dumps({"type": event_type, **data}, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def sse_event_generic(data: dict) -> str:
    """格式化为 SSE 通用格式（不指定 event 类型）"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat/stream")
async def chat_stream(req: ChatRequest):
    """
    客户对话 SSE 端点
    
    工作流程：
    1. 接收用户消息
    2. 运行 Agent 图
    3. 通过 SSE 实时推送状态变化
    4. 同步更新 GlobalStateTracker
    5. 通知管理员 Dashboard
    
    返回的 SSE 事件类型：
    - node_start      : 节点开始执行
    - node_complete   : 节点执行完成（含状态信息）
    - token           : LLM 流式 token
    - token_usage     : Token 消耗统计
    - review_required : 需要人工审批（Agent 中断）
    - message         : 最终回复消息
    - error           : 错误信息
    - done            : 流结束标记
    """
    session_id = req.session_id
    user_id = req.user_id
    config = {"configurable": {"thread_id": session_id}}
    
    # 注册会话
    tracker.register_session(session_id, {
        "user_id": user_id,
        "current_step": "starting",
        "category": "",
        "status": "active",
    })
    
    async def generate():
        try:
            # 推送初始状态
            yield sse_event("node_start", {"node": "authenticate"})
            
            # 更新追踪
            tracker.update_session(session_id, {
                "current_step": "processing",
                "status": "running"
            })
            
            # 构建输入状态
            input_state = {
                "messages": [HumanMessage(content=req.message)],
                "user_id": user_id,
                "session_id": session_id,
            }
            
            # 逐个处理 Agent 事件
            async for event in app.agent_graph.astream_events(
                input_state, config, version="v2"
            ):
                event_type = event.get("event")
                node_name = event.get("name", "")
                event_data = event.get("data", {})

                print(f"Received event: {event_type} from node: {node_name} with data: {event_data}")  # 调试日志
                
                # ===== 节点开始 =====
                if event_type == "on_chain_start" and node_name:
                    tracker.update_session(session_id, {
                        "current_step": node_name,
                    })
                    
                    yield sse_event("node_start", {"node": node_name})
                    
                    # 通知管理员 Dashboard
                    await tracker.broadcast_to_admins({
                        "type": "session_update",
                        "session_id": session_id,
                        "current_step": node_name,
                        "node": node_name,
                        "status": "running",
                    })
                
                # ===== 节点完成 =====
                elif event_type == "on_chain_end" and node_name:
                    output = event_data.get("output", {})
                    
                    if isinstance(output, dict):
                        # 更新分类信息
                        if output.get("category"):
                            tracker.update_session(session_id, {
                                "category": output["category"],
                            })
                        
                        # 检查是否需要人工审批
                        if output.get("type") == "human_review_required":
                            review_data = {
                                "category": output.get("category", ""),
                                "order_id": output.get("order_id", ""),
                                "amount": output.get("amount", 0),
                                "reason": output.get("reason", ""),
                                "risk_level": output.get("risk_level", "medium"),
                                "timestamp": time.time(),
                            }
                            
                            # 加入待审批队列
                            tracker.add_pending_approval(session_id, review_data)
                            
                            tracker.update_session(session_id, {
                                "current_step": "awaiting_approval",
                                "status": "interrupted",
                                "approval_data": review_data,
                            })
                            
                            # 推送给客户端：需要审批
                            yield sse_event("review_required", {
                                "message": "您的请求需要人工审核，请稍候...",
                                "approval_data": review_data,
                            })
                            
                            # 通知管理员 Dashboard
                            await tracker.broadcast_to_admins({
                                "type": "new_approval",
                                "session_id": session_id,
                                "data": review_data,
                            })
                            await tracker.broadcast_to_admins({
                                "type": "dashboard_update",
                                "data": tracker.get_dashboard_data(),
                            })
                            
                            yield sse_event("done", {})
                            return  # 中断流，等审批后再恢复
                        
                        # 有最终回复
                        if output.get("final_response"):
                            tracker.update_session(session_id, {
                                "current_step": "completed",
                                "status": "completed",
                            })
                            
                            yield sse_event("message", {
                                "content": output["final_response"],
                                "approval_status": output.get("approval_status"),
                                "category": output.get("category"),
                            })
                            
                            yield sse_event("node_complete", {
                                "node": node_name,
                                "step": output.get("current_step", node_name),
                            })
                            
                            # 通知管理员
                            await tracker.broadcast_to_admins({
                                "type": "session_update",
                                "session_id": session_id,
                                "current_step": "completed",
                                "status": "completed",
                            })
                            await tracker.broadcast_to_admins({
                                "type": "dashboard_update",
                                "data": tracker.get_dashboard_data(),
                            })
                        else:
                            # 中间节点完成
                            yield sse_event("node_complete", {
                                "node": node_name,
                                "step": output.get("current_step", output.get("next_step", "")),
                            })
                
                # ===== LLM 流式 Token（仅用户可见节点） =====
                elif event_type == "on_chat_model_stream":
                    # 只流式输出 respond/respond_directly 节点的 token，
                    # 跳过 classify/query_order/auto_refund 等内部 LLM 调用
                    langgraph_node = event.get("metadata", {}).get("langgraph_node", "")
                    if langgraph_node in ("respond", "respond_directly"):
                        chunk = event_data.get("chunk", {})
                        if hasattr(chunk, "content") and chunk.content:
                            yield sse_event("token", {"content": chunk.content})

                # ===== LLM 调用完成（Token 统计） =====
                elif event_type == "on_chat_model_end":
                    output = event_data.get("output", {})
                    if hasattr(output, "usage_metadata"):
                        usage = output.usage_metadata
                        input_tokens = usage.get("input_tokens", 0)
                        output_tokens = usage.get("output_tokens", 0)

                        token_info = tracker.add_tokens(
                            input_tokens, output_tokens,
                            os.getenv("LLM_MODEL", "gpt-4o")
                        )

                        yield sse_event("token_usage", {
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "total_tokens": input_tokens + output_tokens,
                            "cost": token_info["cost"],
                        })

                        # 通知管理员 Dashboard Token 更新
                        await tracker.broadcast_to_admins({
                            "type": "token_update",
                            "total_tokens": tracker.total_tokens,
                            "total_cost": tracker.total_cost,
                        })
            
            # 流结束
            yield sse_event("done", {})
            
        except Exception as e:
            tracker.update_session(session_id, {
                "status": "error",
                "error": str(e),
            })
            yield sse_event("error", {"message": str(e)})
            yield sse_event("done", {})
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 禁用 nginx 缓冲
        },
    )


# ========== 审批恢复端点 ==========
@app.post("/approve")
async def approve(req: ApprovalRequest):
    """
    管理员审批接口
    
    流程：
    1. 验证会话是否在中断状态
    2. 注入审批决策
    3. 恢复 Agent 执行
    4. 通知前端（通过 SSE 不可行，改用状态查询）
    """
    session_id = req.session_id
    config = {"configurable": {"thread_id": session_id}}
    
    # 验证是否有待审批任务
    state = await app.agent_graph.aget_state(config)
    
    if state.next == ():
        return {"error": "没有待审批的任务", "session_id": session_id}
    
    # 注入决策
    await app.agent_graph.aupdate_state(
        config,
        {
            "human_feedback": req.comment,
            "approval_status": req.decision,
        },
    )
    
    # 从待审批队列移除
    tracker.remove_pending_approval(session_id)
    
    # 恢复执行
    result = await app.agent_graph.ainvoke(None, config)
    
    final_response = result.get("final_response", "处理完成")
    
    # 更新追踪
    tracker.update_session(session_id, {
        "current_step": "completed",
        "status": "completed",
    })
    
    # 通知管理员 Dashboard
    await tracker.broadcast_to_admins({
        "type": "approval_resolved",
        "session_id": session_id,
        "decision": req.decision,
        "comment": req.comment,
    })
    await tracker.broadcast_to_admins({
        "type": "dashboard_update",
        "data": tracker.get_dashboard_data(),
    })
    
    return {
        "status": "ok",
        "session_id": session_id,
        "decision": req.decision,
        "final_response": final_response,
        "approval_status": result.get("approval_status"),
    }


# ========== 连接审批（审批后获取新 SSE 流） ==========
@app.post("/chat/stream/continue")
async def chat_stream_continue(session_id: str):
    """
    审批后继续获取 SSE 流
    
    当管理员审批后，客户端重新连接此端点，
    获取 Agent 恢复执行后的流式输出。
    """
    config = {"configurable": {"thread_id": session_id}}
    
    async def generate():
        try:
            tracker.update_session(session_id, {
                "current_step": "resuming",
                "status": "running",
            })
            
            async for event in app.agent_graph.astream_events(
                None, config, version="v2"
            ):
                event_type = event.get("event")
                node_name = event.get("name", "")
                event_data = event.get("data", {})
                
                if event_type == "on_chain_start" and node_name:
                    tracker.update_session(session_id, {
                        "current_step": node_name,
                    })
                    
                    yield sse_event("node_start", {"node": node_name})
                    
                    await tracker.broadcast_to_admins({
                        "type": "session_update",
                        "session_id": session_id,
                        "current_step": node_name,
                        "status": "running",
                    })
                
                elif event_type == "on_chain_end" and node_name:
                    output = event_data.get("output", {})
                    
                    if isinstance(output, dict) and output.get("final_response"):
                        tracker.update_session(session_id, {
                            "current_step": "completed",
                            "status": "completed",
                        })
                        
                        yield sse_event("message", {
                            "content": output["final_response"],
                        })
                        
                        await tracker.broadcast_to_admins({
                            "type": "session_update",
                            "session_id": session_id,
                            "current_step": "completed",
                            "status": "completed",
                        })
                
                elif event_type == "on_chat_model_stream":
                    # 只流式输出 respond/respond_directly 节点的 token
                    langgraph_node = event.get("metadata", {}).get("langgraph_node", "")
                    if langgraph_node in ("respond", "respond_directly"):
                        chunk = event_data.get("chunk", {})
                        if hasattr(chunk, "content") and chunk.content:
                            yield sse_event("token", {"content": chunk.content})

                elif event_type == "on_chat_model_end":
                    output = event_data.get("output", {})
                    if hasattr(output, "usage_metadata"):
                        usage = output.usage_metadata
                        token_info = tracker.add_tokens(
                            usage.get("input_tokens", 0),
                            usage.get("output_tokens", 0),
                            os.getenv("LLM_MODEL", "gpt-4o"),
                        )
                        yield sse_event("token_usage", {
                            "input_tokens": token_info["input_tokens"],
                            "output_tokens": token_info["output_tokens"],
                            "total_tokens": token_info["total_tokens"],
                            "cost": token_info["cost"],
                        })

                        await tracker.broadcast_to_admins({
                            "type": "token_update",
                            "total_tokens": tracker.total_tokens,
                            "total_cost": tracker.total_cost,
                        })
            
            yield sse_event("done", {})
            
        except Exception as e:
            yield sse_event("error", {"message": str(e)})
            yield sse_event("done", {})
    
    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )

@app.get("/state/{session_id}")
async def get_state(session_id: str):
    """查询会话状态"""
    config = {"configurable": {"thread_id": session_id}}
    state = await app.agent_graph.aget_state(config)

    return {
        "values": state.values if state.values else {},
        "next": list(state.next) if state.next else [],
        "is_interrupted": state.next != () and len(state.next) > 0 if state.next else False,
    }


@app.get("/state/{session_id}")
async def get_state(session_id: str):
    """查询会话的当前状态"""
    config = {"configurable": {"thread_id": session_id}}
    state = await app.agent_graph.aget_state(config)
    
    return {
        "values": state.values if state.values else {},
        "next": list(state.next) if state.next else [],
        "is_interrupted": state.next != () and len(state.next) > 0 if state.next else False,
    }


@app.get("/dashboard")
async def get_dashboard():
    """HTTP 方式获取 Dashboard 数据"""
    return tracker.get_dashboard_data()


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "active_sessions": len(tracker.active_sessions),
        "pending_approvals": len(tracker.pending_approvals),
        "admin_connections": len(tracker.admin_connections),
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
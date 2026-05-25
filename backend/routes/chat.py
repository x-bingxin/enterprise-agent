"""客户对话路由：/chat, /chat/stream, /approve, /chat/stream/continue"""

import time
import asyncio

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from models import ChatRequest, ApprovalRequest, ContinueRequest
from sse_utils import sse_event, try_count_tokens
from state_tracker import tracker

router = APIRouter()


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """同步对话"""
    agent_graph = request.app.state.agent_graph
    config = {"configurable": {"thread_id": req.session_id}}
    result = await agent_graph.ainvoke(
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


@router.post("/chat/stream")
async def chat_stream(req: ChatRequest, request: Request):
    """
    客户对话 SSE 端点

    通过 SSE 实时推送状态变化，同步更新 GlobalStateTracker，通知管理员 Dashboard。
    """
    agent_graph = request.app.state.agent_graph
    session_id = req.session_id
    user_id = req.user_id
    config = {"configurable": {"thread_id": session_id}}

    tracker.register_session(session_id, {
        "user_id": user_id,
        "current_step": "starting",
        "category": "",
        "status": "active",
    })

    async def generate():
        try:
            yield sse_event("node_start", {"node": "authenticate"})

            tracker.update_session(session_id, {
                "current_step": "processing",
                "status": "running",
            })

            input_state = {
                "messages": [HumanMessage(content=req.message)],
                "user_id": user_id,
                "session_id": session_id,
            }

            async for event in agent_graph.astream_events(
                input_state, config, version="v2"
            ):
                event_type = event.get("event")
                node_name = event.get("name", "")
                event_data = event.get("data", {})

                # ===== 节点开始 =====
                if event_type == "on_chain_start" and node_name:
                    tracker.update_session(session_id, {"current_step": node_name})
                    yield sse_event("node_start", {"node": node_name})

                    await tracker.broadcast_to_admins({
                        "type": "session_update",
                        "session_id": session_id,
                        "current_step": node_name,
                        "node": node_name,
                        "status": "running",
                    })

                # ===== 节点结束 =====
                elif event_type == "on_chain_end" and node_name:
                    output = event_data.get("output", {})
                    yield sse_event("node_end", {"node": node_name})

                    if isinstance(output, dict):
                        if output.get("category"):
                            tracker.update_session(session_id, {"category": output["category"]})

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
                            yield sse_event("node_complete", {
                                "node": node_name,
                                "step": output.get("current_step", output.get("next_step", "")),
                            })

                # ===== Tool 开始/结束 =====
                elif event_type == "on_tool_start":
                    tool_name = event_data.get("tool_name", "")
                    print(f"Tool {tool_name} started with input:", event_data.get("input", {}))

                elif event_type == "on_tool_end":
                    tool_name = event_data.get("tool_name", "")
                    print(f"Tool {tool_name} output:", event_data.get("output", {}))

                # ===== LLM 流式 Token =====
                elif event_type == "on_chat_model_stream":
                    langgraph_node = event.get("metadata", {}).get("langgraph_node", "")
                    chunk = event_data.get("chunk", {})

                    if langgraph_node in ("respond", "respond_directly"):
                        if hasattr(chunk, "content") and chunk.content:
                            yield sse_event("token", {"content": chunk.content})

                    if (token_info := try_count_tokens(chunk)):
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

                elif event_type == "on_chat_model_end":
                    if (token_info := try_count_tokens(event_data.get("output", {}))):
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

            # 检查 graph 是否因 human_review 而中断
            state = await agent_graph.aget_state(config)
            if state.next and state.tasks:
                for task in state.tasks:
                    for iv in task.interrupts:
                        review_data = iv.value
                        if isinstance(review_data, dict) and review_data.get("type") == "human_review_required":
                            tracked_review = {
                                "category": review_data.get("category", ""),
                                "order_id": review_data.get("order_id", ""),
                                "amount": review_data.get("amount", 0),
                                "reason": review_data.get("reason", ""),
                                "risk_level": review_data.get("risk_level", "medium"),
                                "timestamp": time.time(),
                            }

                            tracker.add_pending_approval(session_id, tracked_review)
                            tracker.update_session(session_id, {
                                "current_step": "awaiting_approval",
                                "status": "interrupted",
                                "approval_data": tracked_review,
                            })

                            yield sse_event("review_required", {
                                "message": "您的请求需要人工审核，请稍候...",
                                "approval_data": tracked_review,
                            })

                            await tracker.broadcast_to_admins({
                                "type": "new_approval",
                                "session_id": session_id,
                                "data": tracked_review,
                            })
                            await tracker.broadcast_to_admins({
                                "type": "dashboard_update",
                                "data": tracker.get_dashboard_data(),
                            })

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
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/approve")
async def approve(req: ApprovalRequest, request: Request):
    """
    管理员审批接口

    验证会话是否在中断状态，存储审批决策（不恢复图执行），通知 Dashboard。
    实际的图恢复由 /chat/stream/continue 完成。
    """
    agent_graph = request.app.state.agent_graph
    session_id = req.session_id
    config = {"configurable": {"thread_id": session_id}}

    state = await agent_graph.aget_state(config)

    if state.next == ():
        return {"error": "没有待审批的任务", "session_id": session_id}

    tracker.remove_pending_approval(session_id)
    tracker.store_approval_decision(session_id, {
        "decision": req.decision,
        "comment": req.comment,
    })

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
        "message": "审批决策已记录，等待会话恢复",
    }


@router.post("/chat/stream/continue")
async def chat_stream_continue(req: ContinueRequest, request: Request):
    """
    审批后继续获取 SSE 流

    客户端在收到 review_required 事件后调用此端点。
    服务器阻塞等待管理员审批，然后恢复图执行并流式返回结果。
    """
    agent_graph = request.app.state.agent_graph
    session_id = req.session_id
    config = {"configurable": {"thread_id": session_id}}

    async def generate():
        try:
            # 等待管理员审批决策（最多等待 5 分钟）
            decision = None
            for _ in range(150):  # 150 * 2s = 5 min
                decision = tracker.pop_approval_decision(session_id)
                if decision:
                    break
                await asyncio.sleep(2)

            if not decision:
                yield sse_event("error", {"message": "等待审批超时，请稍后重试"})
                yield sse_event("done", {})
                return

            tracker.update_session(session_id, {
                "current_step": "resuming",
                "status": "running",
            })

            async for event in agent_graph.astream_events(
                Command(resume={"decision": decision["decision"], "comment": decision["comment"]}),
                config,
                version="v2",
            ):
                event_type = event.get("event")
                node_name = event.get("name", "")
                event_data = event.get("data", {})

                if event_type == "on_chain_start" and node_name:
                    tracker.update_session(session_id, {"current_step": node_name})
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

                        yield sse_event("message", {"content": output["final_response"]})

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

                elif event_type == "on_chat_model_stream":
                    langgraph_node = event.get("metadata", {}).get("langgraph_node", "")
                    chunk = event_data.get("chunk", {})

                    if langgraph_node in ("respond", "respond_directly"):
                        if hasattr(chunk, "content") and chunk.content:
                            yield sse_event("token", {"content": chunk.content})

                    if (token_info := try_count_tokens(chunk)):
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

                elif event_type == "on_chat_model_end":
                    if (token_info := try_count_tokens(event_data.get("output", {}))):
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

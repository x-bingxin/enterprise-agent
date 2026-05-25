"""系统路由：/state/{session_id}, /dashboard, /health"""

from fastapi import APIRouter, Request

from state_tracker import tracker

router = APIRouter()


@router.get("/state/{session_id}")
async def get_state(session_id: str, request: Request):
    """查询会话的当前状态"""
    agent_graph = request.app.state.agent_graph
    config = {"configurable": {"thread_id": session_id}}
    state = await agent_graph.aget_state(config)

    return {
        "values": state.values if state.values else {},
        "next": list(state.next) if state.next else [],
        "is_interrupted": state.next != () and len(state.next) > 0 if state.next else False,
    }


@router.get("/dashboard")
async def get_dashboard():
    """HTTP 方式获取 Dashboard 数据"""
    return tracker.get_dashboard_data()


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "active_sessions": len(tracker.active_sessions),
        "pending_approvals": len(tracker.pending_approvals),
        "admin_connections": len(tracker.admin_connections),
    }

"""管理员 WebSocket 路由：/ws/admin-dashboard"""

import time
import json
import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from state_tracker import tracker

router = APIRouter()


@router.websocket("/ws/admin-dashboard")
async def admin_dashboard_websocket(ws: WebSocket):
    """
    管理员 Dashboard WebSocket

    功能：
    - 接收全局会话状态实时更新
    - 接收新的待审批工单通知
    - 支持手动刷新
    """
    agent_graph = ws.app.state.agent_graph
    await ws.accept()
    tracker.register_admin(ws)

    await ws.send_json({
        "type": "dashboard_init",
        "data": tracker.get_dashboard_data(),
    })

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
                await ws.send_json({
                    "type": "dashboard_update",
                    "data": tracker.get_dashboard_data(),
                })

            elif msg.get("type") == "get_session_detail":
                sid = msg.get("session_id")
                if sid:
                    config = {"configurable": {"thread_id": sid}}
                    state = await agent_graph.aget_state(config)
                    await ws.send_json({
                        "type": "session_detail",
                        "session_id": sid,
                        "values": state.values if state.values else {},
                        "is_interrupted": state.next != () and len(state.next) > 0 if state.next else False,
                    })

            elif msg.get("type") == "approval_decision":
                sid = msg.get("session_id")
                decision = msg.get("decision")
                comment = msg.get("comment", "")

                if sid:
                    tracker.remove_pending_approval(sid)

                    await tracker.broadcast_to_admins({
                        "type": "approval_resolved",
                        "session_id": sid,
                        "decision": decision,
                    })

    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[Admin WS] Error: {e}")
    finally:
        heartbeat_task.cancel()
        tracker.unregister_admin(ws)

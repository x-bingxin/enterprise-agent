# ========== 全局状态追踪器 ==========
import time
from typing import Dict, Set

from fastapi import WebSocket


class GlobalStateTracker:
    """
    全局状态追踪器
    
    负责：
    - 追踪所有活跃会话的状态
    - 管理待审批工单队列
    - 累计 Token 消耗和成本
    - 向管理员 Dashboard 广播状态更新
    """
    
    def __init__(self):
        # session_id → 会话元信息
        self.active_sessions: Dict[str, dict] = {}
        # admin WebSocket 连接集合
        self.admin_connections: Set[WebSocket] = set()
        # 全局 token 统计
        self.total_tokens: int = 0
        self.total_cost: float = 0.0
        # 待审批队列
        self.pending_approvals: Dict[str, dict] = {}
        # Token 单价（美元/1K tokens）
        self.token_prices = {
            "gpt-4o": {"input": 0.005, "output": 0.015},
            "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
            "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
        }

    # ===== 会话管理 =====
    def register_session(self, session_id: str, data: dict):
        """注册新会话或更新已有会话"""
        data["last_updated"] = time.time()
        if session_id in self.active_sessions:
            self.active_sessions[session_id].update(data)
        else:
            self.active_sessions[session_id] = data

    def update_session(self, session_id: str, data: dict):
        """更新会话状态"""
        if session_id in self.active_sessions:
            self.active_sessions[session_id].update(data)
            self.active_sessions[session_id]["last_updated"] = time.time()

    def remove_session(self, session_id: str):
        """移除已结束的会话"""
        self.active_sessions.pop(session_id, None)
        # 同时清理该会话的待审批工单
        self.pending_approvals.pop(session_id, None)

    # ===== Token 统计 =====
    def add_tokens(self, input_tokens: int, output_tokens: int, model: str = "gpt-4o"):
        """累计 Token 消耗并估算成本"""
        self.total_tokens += (input_tokens + output_tokens)
        
        prices = self.token_prices.get(model, self.token_prices["gpt-4o"])
        cost = (input_tokens / 1000) * prices["input"] + \
               (output_tokens / 1000) * prices["output"]
        self.total_cost += cost
        
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cost": round(cost, 6),
            "cumulative_tokens": self.total_tokens,
            "cumulative_cost": round(self.total_cost, 4),
        }

    # ===== 审批管理 =====
    def add_pending_approval(self, session_id: str, approval_data: dict):
        """添加待审批工单"""
        approval_data["session_id"] = session_id
        approval_data["created_at"] = time.time()
        self.pending_approvals[session_id] = approval_data

    def remove_pending_approval(self, session_id: str):
        """移除已处理的审批"""
        self.pending_approvals.pop(session_id, None)

    def get_pending_approvals_list(self) -> list:
        """获取待审批工单列表"""
        return [
            {
                "session_id": sid,
                **data
            }
            for sid, data in self.pending_approvals.items()
        ]

    # ===== 管理员广播 =====
    async def broadcast_to_admins(self, message: dict):
        """向所有管理员 Dashboard 广播消息"""
        disconnected = set()
        for ws in self.admin_connections:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.add(ws)
        
        # 清理断开的连接
        self.admin_connections -= disconnected

    def register_admin(self, ws: WebSocket):
        """注册管理员连接"""
        self.admin_connections.add(ws)

    def unregister_admin(self, ws: WebSocket):
        """注销管理员连接"""
        self.admin_connections.discard(ws)

    # ===== Dashboard 数据 =====
    def get_dashboard_data(self) -> dict:
        """获取 Dashboard 完整数据"""
        return {
            "active_sessions_count": len(self.active_sessions),
            "pending_approvals_count": len(self.pending_approvals),
            "total_tokens": self.total_tokens,
            "total_cost": round(self.total_cost, 4),
            "active_sessions": {
                sid: {
                    "current_step": info.get("current_step", ""),
                    "category": info.get("category", ""),
                    "user_id": info.get("user_id", "anonymous"),
                    "status": info.get("status", "unknown"),
                    "last_updated": info.get("last_updated", 0),
                }
                for sid, info in self.active_sessions.items()
            },
            "pending_approvals": self.get_pending_approvals_list(),
        }
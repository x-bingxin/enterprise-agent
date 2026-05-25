# backend/graph/nodes.py (完全修正版)
import json
from langgraph.types import interrupt
from langchain_core.tools import tool, BaseTool
from pydantic import BaseModel, Field
from typing import List

# ========== 定义业务工具（LangChain BaseTool） ==========

class RefundProcessInput(BaseModel):
    order_id: str = Field(description="退款订单号")
    amount: float = Field(description="退款金额")
    reason: str = Field(description="退款原因")

@tool(args_schema=RefundProcessInput)
async def process_refund(order_id: str, amount: float, reason: str) -> str:
    """处理退款申请"""
    # 实际应调用支付系统API
    result = {
        "status": "success",
        "refund_id": f"RF-{order_id}-{int(amount)}",
        "order_id": order_id,
        "amount": amount,
        "reason": reason,
        "processed_at": "2025-01-15T10:30:00",
        "estimated_arrival": "3-5个工作日"
    }
    return json.dumps(result, ensure_ascii=False)

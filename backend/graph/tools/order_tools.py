# backend/graph/nodes.py (完全修正版)
import json
from langgraph.types import interrupt
from langchain_core.tools import tool, BaseTool
from pydantic import BaseModel, Field
from typing import List

# ========== 定义业务工具（LangChain BaseTool） ==========

class OrderQueryInput(BaseModel):
    order_id: str = Field(description="订单号")

@tool(args_schema=OrderQueryInput)
async def lookup_order(order_id: str) -> str:
    """查询订单详细信息，包括状态、商品、物流"""
    mock_orders = {
        "ORD-001": {
            "order_id": "ORD-001",
            "status": "已发货",
            "items": [{"name": "机械键盘", "qty": 1, "price": 399}],
            "total": 399,
            "shipping_address": "北京市朝阳区xxx路xxx号",
            "tracking": {"company": "顺丰", "number": "SF1234567890"},
            "estimated_delivery": "2025-01-22",
            "payment_method": "微信支付"
        },
        "ORD-002": {
            "order_id": "ORD-002",
            "status": "待发货",
            "items": [{"name": "4K显示器", "qty": 1, "price": 4999}],
            "total": 4999,
            "shipping_address": "上海市浦东新区xxx",
            "tracking": None,
            "estimated_delivery": "2025-01-25",
            "payment_method": "支付宝"
        }
    }
    order_id = order_id.strip().upper()
    order = mock_orders.get(order_id, {"error": f"订单 {order_id} 不存在"})
    return json.dumps(order, ensure_ascii=False)
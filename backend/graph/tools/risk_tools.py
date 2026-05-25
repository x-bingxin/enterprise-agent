# backend/graph/nodes.py (完全修正版)
import json
from langgraph.types import interrupt
from langchain_core.tools import tool, BaseTool
from pydantic import BaseModel, Field
from typing import List

# ========== 定义业务工具（LangChain BaseTool） ==========

class RiskCheckInput(BaseModel):
    order_id: str = Field(description="订单号")
    amount: float = Field(description="金额")
    reason: str = Field(description="退款原因")

@tool(args_schema=RiskCheckInput)
async def check_risk(order_id: str, amount: float, reason: str) -> str:
    """评估退款风险等级"""
    risk_score = 0
    flags = []
    
    if amount > 10000:
        risk_score += 50
        flags.append("大额退款")
    elif amount > 1000:
        risk_score += 20
    
    if any(kw in reason for kw in ["损坏", "质量", "假货", "欺诈"]):
        risk_score += 30
        flags.append("敏感原因")
    
    if "频繁退款" in reason:
        risk_score += 40
        flags.append("频繁退款")
    
    if risk_score >= 60:
        level = "high"
        auto = False
    elif risk_score >= 30:
        level = "medium"
        auto = False
    else:
        level = "low"
        auto = True
    
    return json.dumps({
        "risk_level": level,
        "risk_score": risk_score,
        "auto_approvable": auto,
        "flags": flags
    }, ensure_ascii=False)
from typing import Annotated, Any, Dict, List, Literal, Optional, TypedDict
from langgraph.graph import add_messages
from pydantic import BaseModel


class TicketCategory(str):
    ORDER_QUERY = "order_query"      # 查订单
    ORDER_MODIFY = "order_modify"    # 改订单
    REFUND_LOW = "refund_low"        # 小额退款(自动)
    REFUND_HIGH = "refund_high"      # 大额退款(人工)
    COMPLAINT = "complaint"          # 投诉(人工)
    ACCOUNT_ISSUE = "account_issue"  # 账号问题

class RiskAssessment(BaseModel):
    risk_level: Literal["low", "medium", "high", "critical"]
    auto_approvable: bool
    required_approvals: int  # 需要几级审批
    flags: List[str]         # 风险标记


class CustomerServiceState(TypedDict):
    """企业客服Agent的全状态"""
    # 对话
    messages: Annotated[list, add_messages]
    
    # 用户信息
    user_id: str
    session_id: str
    authenticated: bool
    
    # 意图与分类
    intent: str
    category: str
    confidence: float
    
    # 业务数据
    order_id: Optional[str]
    order_details: Optional[Dict[str, Any]]
    refund_amount: Optional[float]
    refund_reason: Optional[str]
    
    # 风险与审批
    risk_assessment: Optional[Dict[str, Any]]
    needs_human_review: bool
    approval_status: Literal["pending", "approved", "rejected", "not_needed"]
    human_feedback: Optional[str]
    
    # 流程控制
    current_step: str
    next_step: str
    iteration_count: int
    error_count: int
    
    # 最终输出
    final_response: Optional[str]
    actions_taken: List[str]
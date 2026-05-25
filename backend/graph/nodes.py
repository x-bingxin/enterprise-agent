# backend/graph/nodes.py (完全修正版)
"""
企业审批 Agent 的核心节点
所有 LLM 调用统一使用 LangChain ChatOpenAI + 消息对象
"""
import json
import re
from typing import Dict, Any
from langgraph.types import interrupt
from langchain_core.messages import (
    HumanMessage, AIMessage, SystemMessage, ToolMessage
)
from langchain_openai import ChatOpenAI
from langchain_core.tools import tool, BaseTool
from pydantic import BaseModel, Field
from typing import Literal, List, Optional

from .state import CustomerServiceState, RiskAssessment

from .tools import lookup_order, process_refund, check_risk

# ========== 节点 1：认证 ==========
async def authenticate_node(state: CustomerServiceState) -> dict:
    """用户认证节点"""
    user_id = state.get("user_id", "anonymous")
    
    if user_id == "anonymous":
        return {
            "next_step": "require_login",
            "final_response": "请先登录后再进行订单操作。您可以输入手机号快速登录。"
        }
    
    return {
        "authenticated": True,
        "next_step": "classify"
    }


# ========== 节点 2：意图分类 ==========
async def classify_intent_node(
    state: CustomerServiceState,
    llm: ChatOpenAI  # ✅ 接收 LangChain ChatOpenAI 实例
) -> dict:
    """LLM 驱动的意图分类"""
    messages = state["messages"]
    
    # 构建分类 prompt
    classification_prompt = """分析用户的客服请求，输出 JSON 格式的分类结果。

分类选项：
- order_query: 查询订单状态/详情/物流
- order_modify: 修改订单（地址、商品等）
- refund_low: 小额退款（金额明确小于500元）
- refund_high: 大额退款（金额>=500元或金额不明但涉及退款）
- complaint: 投诉/不满/要求赔偿
- account_issue: 账号相关问题
- general_chat: 问候/闲聊/其他

输出格式（严格 JSON）：
{"category": "...", "confidence": 0.0, "reason": "..."}"""

    classify_messages = [
        SystemMessage(content=classification_prompt),
        HumanMessage(content=f"用户消息: {messages[-1].content if hasattr(messages[-1], 'content') else str(messages[-1])}")
    ]
    try:
        # ✅ 使用 LangChain 的 ainvoke
        response = await llm.ainvoke(classify_messages)
        print("classify response:", response)
        result_text = response.content
    except Exception as e:
        print("LLM 分类失败:", e)
    
    # 提取 JSON
    json_match = re.search(r'\{.*\}', result_text, re.DOTALL)
    if json_match:
        result = json.loads(json_match.group())
    else:
        result = {"category": "general_chat", "confidence": 0.5, "reason": "解析失败"}
    
    category = result["category"]
    needs_human = category in ["refund_high", "complaint"]
    
    return {
        "intent": category,
        "category": category,
        "confidence": result["confidence"],
        "needs_human_review": needs_human,
        "approval_status": "pending" if needs_human else "not_needed",
        "next_step": "route_by_category"
    }


# ========== 节点 3：路由分发 ==========
def route_by_category_node(state: CustomerServiceState) -> dict:
    """根据意图路由"""
    category = state["category"]
    
    routing_map = {
        "order_query": "query_order",
        "order_modify": "modify_order",
        "refund_low": "auto_refund",
        "refund_high": "human_review",
        "complaint": "human_review",
        "account_issue": "account_service",
        "general_chat": "respond_directly"
    }
    
    next_step = routing_map.get(category, "respond_directly")
    return {"next_step": next_step, "current_step": "routing"}


# ========== 节点 4：查询订单（使用 Tool 调用） ==========
async def query_order_node(
    state: CustomerServiceState,
    llm: ChatOpenAI  # ✅ 绑定工具的 LLM
) -> dict:
    """使用 LLM + Tool 查询订单"""
    messages = list(state["messages"])
    
    # 让 LLM 提取订单号并调用工具
    extract_prompt = SystemMessage(content="""从用户消息中提取订单号。
如果用户提到了订单号，调用 lookup_order 工具查询。
如果用户没有明确订单号，询问用户提供订单号。""")
    
    # ✅ 使用绑定工具的 LLM
    llm_with_tools = llm.bind_tools([lookup_order])
    
    response = await llm_with_tools.ainvoke([extract_prompt] + messages)
    
    # 检查是否需要调工具
    if hasattr(response, 'tool_calls') and response.tool_calls:
        # 执行工具调用
        tool_results = []
        for tool_call in response.tool_calls:
            if tool_call['name'] == 'lookup_order':
                result = await lookup_order.ainvoke(tool_call['args'])
                tool_results.append(ToolMessage(
                    content=str(result),
                    tool_call_id=tool_call['id']
                ))
                # 解析结果
                order_data = json.loads(str(result))
                if "error" not in order_data:
                    return {
                        "order_id": order_data.get("order_id"),
                        "order_details": order_data,
                        "messages": [response] + tool_results,
                        "next_step": "respond",
                        "current_step": "order_queried"
                    }
        
        return {
            "messages": [response] + tool_results,
            "next_step": "respond",
            "current_step": "order_queried"
        }
    
    # LLM 没有调工具，直接返回
    return {
        "messages": [response],
        "next_step": "respond",
        "current_step": "no_order_found"
    }


# ========== 节点 5：自动退款 ==========
async def auto_refund_node(
    state: CustomerServiceState,
    llm: ChatOpenAI
) -> dict:
    """小额退款自动处理"""
    messages = state["messages"]
    
    # 提取退款信息
    extract_prompt = SystemMessage(content="""从用户消息中提取退款信息：
- 订单号
- 退款金额
- 退款原因
如果信息不全，请询问用户补充。
如果信息齐全，调用 check_risk 进行风险评估。""")
    
    llm_with_tools = llm.bind_tools([check_risk, process_refund])
    response = await llm_with_tools.ainvoke([extract_prompt] + list(messages))
    
    # 处理工具调用
    if hasattr(response, 'tool_calls') and response.tool_calls:
        all_results = []
        risk_assessment = None
        
        for tool_call in response.tool_calls:
            if tool_call['name'] == 'check_risk':
                result = await check_risk.ainvoke(tool_call['args'])
                all_results.append(ToolMessage(
                    content=str(result),
                    tool_call_id=tool_call['id']
                ))
                risk_assessment = json.loads(str(result))
            
            elif tool_call['name'] == 'process_refund':
                if risk_assessment and risk_assessment.get("auto_approvable"):
                    result = await process_refund.ainvoke(tool_call['args'])
                    all_results.append(ToolMessage(
                        content=str(result),
                        tool_call_id=tool_call['id']
                    ))
                    return {
                        "risk_assessment": risk_assessment,
                        "actions_taken": ["auto_refund_processed"],
                        "approval_status": "approved",
                        "messages": [response] + all_results,
                        "next_step": "respond",
                    }
                else:
                    # 风险高于预期，转人工
                    return {
                        "risk_assessment": risk_assessment,
                        "needs_human_review": True,
                        "approval_status": "pending",
                        "messages": [response] + all_results,
                        "next_step": "human_review",
                    }
        
        return {
            "messages": [response] + all_results,
            "next_step": "respond"
        }
    
    return {
        "messages": [response],
        "next_step": "respond"
    }


# ========== 节点 6：人工审批（HITL） ==========
async def human_review_node(state: CustomerServiceState) -> dict:
    """高风险操作 — 中断等待人工审批"""
    # 从状态中提取审批所需数据
    review_data = {
        "type": "human_review_required",
        "category": state["category"],
        "order_id": state.get("order_id", "未知"),
        "amount": state.get("refund_amount", 
                 state.get("order_details", {}).get("total", 0)),
        "reason": state.get("refund_reason", "用户未说明"),
        "risk_level": state.get("risk_assessment", {}).get("risk_level", "medium"),
        "timestamp": "2025-01-15T10:30:00"
    }
    
    # ⚠️ 中断！外部系统拿到 review_data
    human_decision = interrupt(review_data)
    
    # 恢复后拿到决策
    return {
        "human_feedback": human_decision.get("comment", ""),
        "approval_status": human_decision.get("decision", "rejected"),
        "next_step": "execute_decision",
        "current_step": "human_reviewed"
    }


# ========== 节点 7：执行决策 ==========
async def execute_decision_node(
    state: CustomerServiceState,
    llm: ChatOpenAI
) -> dict:
    """执行审批结果"""
    if state["approval_status"] == "approved":
        # 执行退款
        refund_amount = state.get("refund_amount", 
                       state.get("order_details", {}).get("total", 0))
        
        # 如果有订单号，调用退款工具
        if state.get("order_id"):
            refund_result = await process_refund.ainvoke({
                "order_id": state["order_id"],
                "amount": refund_amount,
                "reason": state.get("refund_reason", "人工审批通过")
            })
            actions = ["refund_processed", "notification_sent"]
            response = f"已为您处理退款，款项将在3-5个工作日内退回原支付方式。退款金额：¥{refund_amount}"
        else:
            actions = ["approved_without_refund"]
            response = "已批准您的请求。"
    else:
        actions = ["refund_rejected", "notification_sent"]
        response = f"抱歉，您的申请未被批准。原因：{state.get('human_feedback', '请提供更多信息')}"
    
    return {
        "actions_taken": actions,
        "final_response": response,
        "next_step": "end"
    }


# ========== 节点 8：响应生成 ==========
async def respond_node(
    state: CustomerServiceState,
    llm: ChatOpenAI  # ✅ LangChain ChatOpenAI
) -> dict:
    """LLM 生成最终回复"""
    
    # 构建上下文
    context = f"""
当前状态：
- 意图：{state.get('category', 'unknown')}
- 订单号：{state.get('order_id', 'N/A')}
- 订单详情：{json.dumps(state.get('order_details', {}), ensure_ascii=False)}
- 审批状态：{state.get('approval_status', 'not_needed')}
- 已执行操作：{state.get('actions_taken', [])}
- 风险等级：{state.get('risk_assessment', {}).get('risk_level', 'none')}
"""

    # 构建消息
    prompt_messages = [
        SystemMessage(content=f"""你是企业客服助手。基于以下上下文生成专业友好的回复。

{context}

要求：
- 语气专业友好
- 如果是退款，明确告知处理时间和方式
- 如需人工审批，明确告知等待时间
- 不要透露内部审批流程细节
- 不要编造不存在的订单信息"""),
    ]
    
    # 添加对话历史（最近3轮）
    history = state["messages"][-6:]  # 最近3轮对话
    prompt_messages.extend(history)
    
    prompt_messages.append(HumanMessage(content="请生成给用户的最终回复"))
    
    # ✅ 使用 LangChain 的 ainvoke
    response = await llm.ainvoke(prompt_messages)
    
    return {
        "final_response": response.content,
        "messages": [response],
        "next_step": "end"
    }


# ========== 节点 9：直接回复（闲聊等） ==========
async def respond_directly_node(
    state: CustomerServiceState,
    llm: ChatOpenAI
) -> dict:
    """处理非业务类对话"""
    messages = state["messages"]
    
    system_prompt = SystemMessage(content="""你是企业客服助手。用户的问题不涉及订单操作。
请友好回复，如果用户有潜在的业务需求，引导其表达。""")
    
    response = await llm.ainvoke([system_prompt] + list(messages))
    
    return {
        "final_response": response.content,
        "messages": [response],
        "next_step": "end"
    }

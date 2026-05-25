# backend/graph/builder.py (完全修正版)
"""
企业审批 Agent 图构建
使用 LangChain ChatOpenAI + 正确的节点签名
"""
import sqlite3
from functools import partial
from langgraph.graph import StateGraph, END
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langchain_openai import ChatOpenAI

from .state import CustomerServiceState
from .nodes import (
    authenticate_node,
    classify_intent_node,
    route_by_category_node,
    query_order_node,
    auto_refund_node,
    human_review_node,
    execute_decision_node,
    respond_node,
    respond_directly_node,
)
# from .tools import order_tools, payment_tools, risk_tools  # 导入工具，确保它们被注册


def build_enterprise_agent(
    llm: ChatOpenAI,
    checkpointer: AsyncSqliteSaver
):
    """
    构建企业审批 Agent

    Args:
        llm: LangChain ChatOpenAI 实例（建议已经设置好 model 和 temperature）
        db_path: SQLite checkpoint 数据库路径
    """
    builder = StateGraph(CustomerServiceState)

    # ========== 添加节点 ==========
    # 注意：需要 LLM 的节点用 partial 注入 llm 参数
    builder.add_node("authenticate", authenticate_node)
    builder.add_node("classify", partial(classify_intent_node, llm=llm))
    builder.add_node("route", route_by_category_node)
    builder.add_node("query_order", partial(query_order_node, llm=llm))
    builder.add_node("auto_refund", partial(auto_refund_node, llm=llm))
    builder.add_node("human_review", human_review_node)
    builder.add_node("execute_decision", partial(execute_decision_node, llm=llm))
    builder.add_node("respond", partial(respond_node, llm=llm))
    builder.add_node("respond_directly", partial(respond_directly_node, llm=llm))

    # ========== 设置入口 ==========
    builder.set_entry_point("authenticate")

    # ========== 边和条件边 ==========

    # 认证 → 分类（或结束）
    builder.add_conditional_edges(
        "authenticate",
        lambda s: s["next_step"],
        {
            "classify": "classify",
            "require_login": END
        }
    )

    # 分类 → 路由
    builder.add_edge("classify", "route")

    # 路由 → 各业务节点
    builder.add_conditional_edges(
        "route",
        lambda s: s["next_step"],
        {
            "query_order": "query_order",
            "auto_refund": "auto_refund",
            "human_review": "human_review",
            "modify_order": "respond",
            "account_service": "respond",
            "respond_directly": "respond_directly",
        }
    )

    # 业务节点 → 响应
    builder.add_edge("query_order", "respond")
    builder.add_edge("auto_refund", "respond")

    # 人工审批 → 执行决策 → 响应
    builder.add_edge("human_review", "execute_decision")
    builder.add_edge("execute_decision", "respond")

    # 响应 → 结束
    builder.add_edge("respond", END)
    builder.add_edge("respond_directly", END)

    # checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)
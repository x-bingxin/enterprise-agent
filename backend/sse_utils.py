"""SSE 事件格式化和 Token 统计工具"""

import json
import os

from state_tracker import tracker


def sse_event(event_type: str, data: dict) -> str:
    """格式化为 SSE 事件"""
    payload = json.dumps({"type": event_type, **data}, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n"


def sse_event_generic(data: dict) -> str:
    """格式化为 SSE 通用格式（不指定 event 类型）"""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def try_count_tokens(output) -> dict | None:
    """
    尝试从 LangChain 消息对象中提取 token 用量。
    流式模式下 usage_metadata 在最后一个 AIMessageChunk 上，
    非流式模式下在 AIMessage 上。都尝试。
    """
    usage = None
    if hasattr(output, "usage_metadata") and output.usage_metadata is not None:
        usage = output.usage_metadata
    elif hasattr(output, "response_metadata"):
        usage = output.response_metadata.get("token_usage")
    if not usage:
        return None
    return tracker.add_tokens(
        usage.get("input_tokens", 0),
        usage.get("output_tokens", 0),
        os.getenv("LLM_MODEL", "gpt-4o"),
    )

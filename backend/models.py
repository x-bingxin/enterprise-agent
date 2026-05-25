"""请求模型"""

from pydantic import BaseModel


class ChatRequest(BaseModel):
    message: str
    user_id: str = "anonymous"
    session_id: str = "default"


class ApprovalRequest(BaseModel):
    session_id: str
    decision: str  # "approved" or "rejected"
    comment: str = ""


class ContinueRequest(BaseModel):
    session_id: str

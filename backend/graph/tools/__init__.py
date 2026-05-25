from typing import List
from langchain.tools import BaseTool

from .order_tools import lookup_order
from .payment_tools import process_refund
from .risk_tools import check_risk

# 汇总工具列表
BUSINESS_TOOLS: List[BaseTool] = [lookup_order, process_refund, check_risk]
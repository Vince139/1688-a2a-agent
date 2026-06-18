"""
Classifier Agent - 询盘分类器
负责任务：
1. 接收 Entry Agent 发送的询盘
2. 分析询盘意图（价格/样品/定制/其他）
3. 评估置信度
4. 路由到对应的 Expert Agent
"""

import os
import json
import logging
import asyncio
from typing import Optional, Any
from dataclasses import dataclass, field
from enum import Enum

import yaml
import aiohttp
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.a2a_client import A2AClientManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Classifier Agent - 1688 Inquiry Classifier")

# ==================== 配置 ====================
with open(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "config.yaml")) as f:
    config = yaml.safe_load(f)

A2A_ENDPOINTS = config["a2a"]["agents"]

a2a_client = A2AClientManager()
for name, url in A2A_ENDPOINTS.items():
    a2a_client.register(name, url)


# ==================== 数据模型 ====================

class InquiryType(str, Enum):
    PRICE = "price"           # 价格咨询
    SAMPLE = "sample"         # 样品咨询
    CUSTOMIZE = "customize"   # 定制/OEM
    OTHER = "other"           # 其他类型
    MIXED = "mixed"           # 混合类型


class ClassificationResult(BaseModel):
    primary_type: InquiryType
    confidence: float
    secondary_types: list[InquiryType] = []
    routing: dict[str, Any]
    summary: str


# ==================== 分类逻辑 ====================

PRICE_KEYWORDS = ["价格", "多少钱", "报价", "批发", "优惠", "折扣", "批量", "拿货", "进货", "进货价", "出厂价", "代理价", "拿货价", "多少钱一个", "单价", "套装价格", "零售价", "批发价"]
SAMPLE_KEYWORDS = ["样品", "拿样", "看样", "试卖", "试用", "寄样", "样本", "样板", "色板", "样衣", "样鞋", "样包", "先买几个试试", "先拿一件"]
CUSTOMIZE_KEYWORDS = ["定制", "OEM", "ODM", "贴牌", "logo", "印字", "印花", "定制颜色", "定做", "改款", "开模", "专版", "专款", "定牌", "来样加工", "logo定制", "品牌定制"]


def classify_inquiry(inquiry_content: str, product_name: str = "") -> ClassificationResult:
    """分析询盘内容，判断意图类型"""
    text = (inquiry_content + " " + product_name).lower()

    price_score = sum(1 for kw in PRICE_KEYWORDS if kw.lower() in text)
    sample_score = sum(1 for kw in SAMPLE_KEYWORDS if kw.lower() in text)
    customize_score = sum(1 for kw in CUSTOMIZE_KEYWORDS if kw.lower() in text)

    scores = {
        InquiryType.PRICE: price_score,
        InquiryType.SAMPLE: sample_score,
        InquiryType.CUSTOMIZE: customize_score,
    }

    # 找最高分
    max_score = max(scores.values())
    if max_score == 0:
        return ClassificationResult(
            primary_type=InquiryType.OTHER,
            confidence=0.5,
            secondary_types=[],
            routing={"agents": ["price_expert"]},
            summary="无法明确分类，转为通用处理"
        )

    primary = max(scores, key=scores.get)
    confidence = min(0.95, 0.5 + (max_score / 5) * 0.45)

    # 次要类型
    secondary = [t for t, s in scores.items() if t != primary and s > 0]

    # 路由配置
    if primary == InquiryType.PRICE:
        agents = ["price_expert"]
        if InquiryType.SAMPLE in secondary:
            agents.append("sample_expert")
        if InquiryType.CUSTOMIZE in secondary:
            agents.append("customize_expert")
    elif primary == InquiryType.SAMPLE:
        agents = ["sample_expert"]
        if InquiryType.PRICE in secondary:
            agents.append("price_expert")
    elif primary == InquiryType.CUSTOMIZE:
        agents = ["customize_expert"]
        if InquiryType.PRICE in secondary:
            agents.append("price_expert")
    else:
        agents = ["price_expert"]

    return ClassificationResult(
        primary_type=primary,
        confidence=confidence,
        secondary_types=secondary,
        routing={"agents": agents},
        summary=f"主要类型: {primary.value}，置信度: {confidence:.0%}，路由到: {', '.join(agents)}"
    )


# ==================== FastAPI 路由 ====================

@app.get("/health")
async def health():
    return {"status": "ok", "agent": "classifier"}


@app.get("/.well-known/agent.json")
async def agent_card():
    return {
        "name": "Classifier Agent",
        "description": "1688询盘分类器 - 分析询盘意图并路由到对应专家",
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": True,
            "supportedTasks": ["classify_inquiry"]
        },
        "endpoint": f"http://classifier-agent:8002"
    }


@app.post("/classify")
async def classify(request: Request):
    """接收询盘并分类"""
    body = await request.json()
    inquiry_content = body.get("inquiry_content", "")
    product_name = body.get("product_name", "")
    task_id = body.get("task_id", "")

    logger.info(f"[Classifier] 分类任务 {task_id}: {inquiry_content[:50]}...")

    result = classify_inquiry(inquiry_content, product_name)

    logger.info(f"[Classifier] 结果: {result.summary}")

    return {
        "task_id": task_id,
        "classification": {
            "primary_type": result.primary_type.value,
            "confidence": result.confidence,
            "secondary_types": [t.value for t in result.secondary_types],
        },
        "routing": result.routing,
        "summary": result.summary
    }


@app.post("/route")
async def route(request: Request):
    """分类并直接路由到各 Expert Agent"""
    body = await request.json()
    inquiry_content = body.get("inquiry_content", "")
    product_name = body.get("product_name", "")
    buyer_name = body.get("buyer_name", "")
    task_id = body.get("task_id", "")

    result = classify_inquiry(inquiry_content, product_name)

    # 并行发送到所有需要路由的 Expert
    expert_tasks = []
    for agent_name in result.routing["agents"]:
        payload = {
            "task_id": task_id,
            "inquiry_content": inquiry_content,
            "product_name": product_name,
            "buyer_name": buyer_name,
            "inquiry_type": result.primary_type.value
        }
        expert_tasks.append(
            a2a_client.send_message(agent_name, json.dumps(payload))
        )

    # 等待所有 Expert 返回
    expert_results = await asyncio.gather(*expert_tasks, return_exceptions=True)

    # 汇总结果
    expert_responses = {}
    for agent_name, resp in zip(result.routing["agents"], expert_results):
        if isinstance(resp, Exception):
            expert_responses[agent_name] = {"error": str(resp)}
        else:
            expert_responses[agent_name] = resp

    return {
        "task_id": task_id,
        "classification": {
            "primary_type": result.primary_type.value,
            "confidence": result.confidence,
        },
        "routing": result.routing,
        "expert_responses": expert_responses
    }


# ==================== 启动 ====================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("AGENT_PORT", 8002))
    uvicorn.run(app, host="0.0.0.0", port=port)

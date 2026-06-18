"""
Entry Agent - 1688 询盘入口
负责任务：
1. 接收 1688 平台的 webhook 回调
2. 解析询盘消息结构
3. 通过 A2A 协议将任务发送给 Classifier Agent
4. 调用 Hermes 进行 AI 增强处理
"""

import os
import json
import logging
import asyncio
from datetime import datetime
from typing import Optional, Any
from dataclasses import dataclass, field

import yaml
import aiohttp
from fastapi import FastAPI, Request, HTTPException
from pydantic import BaseModel

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.a2a_client import A2AClientManager
from shared.hermes_client import HermesClient

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Entry Agent - 1688 Gateway")

# ==================== 配置 ====================
_config = None


def get_config():
    global _config
    if _config is None:
        config_path = os.path.join(
            os.path.dirname(__file__),
            "..",
            "..",
            "shared",
            "config.yaml"
        )
        with open(config_path) as f:
            _config = yaml.safe_load(f)
    return _config


config = get_config()

A2A_ENDPOINTS = config["a2a"]["agents"]
REPLY_CONFIG = config.get("reply", {})

a2a_client = A2AClientManager()
for name, url in A2A_ENDPOINTS.items():
    a2a_client.register(name, url)

# Hermes 客户端
hermes_client = HermesClient()


# ==================== 数据模型 ====================

@dataclass
class InquiryContext:
    """询盘上下文"""
    inquiry_id: str
    buyer_id: str
    buyer_name: str
    product_name: str
    product_category: str
    inquiry_content: str
    inquiry_type: str  # "price" | "sample" | "customization" | "other"
    timestamp: str
    trace: list = field(default_factory=list)


class InquiryRequest(BaseModel):
    """1688 询盘请求"""
    message_id: str
    buyer_id: str
    buyer_name: str
    product_name: str
    product_url: str = ""
    content: str
    timestamp: str = ""
    raw: dict = {}


class InquiryResponse(BaseModel):
    """处理结果响应"""
    success: bool
    inquiry_id: str
    reply_type: str  # "auto" | "human_review" | "pending"
    confidence: float
    reply_preview: str
    message: str


# ==================== 询盘分类器 ====================

class InquiryClassifier:
    """询盘分类器 - 决定路由到哪个 Expert"""

    # 分类关键词
    TYPE_KEYWORDS = {
        "price": [
            "多少钱", "价格", "报价", "优惠", "折扣", "便宜",
            "怎么卖", "多少", "批发价", "拿货价", "批量价",
            "单价", "总价", "含税", "不含税", "成本"
        ],
        "sample": [
            "样品", "拿样", "看样", "样本", "打样", "色样",
            "样品费", "样品价格", "寄样", "样品确认",
            "样衣", "样板", "样品申请"
        ],
        "customization": [
            "定制", "贴牌", "oem", "odm", "logo", "品牌",
            "包装", "规格", "颜色", "尺寸", "材质",
            "自主设计", "开模", "定做", "改款",
            "加印", "印字", "丝印", "绣花"
        ]
    }

    @classmethod
    def classify(cls, text: str, product_name: str = "") -> str:
        """
        分类询盘类型

        Returns:
            "price" | "sample" | "customization" | "other"
        """

        combined_text = f"{text} {product_name}".lower()

        scores = {}

        for inquiry_type, keywords in cls.TYPE_KEYWORDS.items():
            score = 0
            for kw in keywords:
                if kw.lower() in combined_text:
                    score += 1
            scores[inquiry_type] = score

        # 找出最高分
        if not scores or max(scores.values()) == 0:
            return "other"

        # 类型优先级：customization > sample > price > other
        priority = ["customization", "sample", "price", "other"]

        best_type = "other"
        best_score = 0

        for t in priority:
            if scores.get(t, 0) > best_score:
                best_score = scores[t]
                best_type = t

        # 阈值判断
        if best_score < 1:
            return "other"

        return best_type


# ==================== 任务跟踪器 ====================

class TaskTracker:
    """任务状态跟踪器"""

    def __init__(self):
        self.tasks = {}
        self.state_file = "/tmp/1688_tasks.json"
        self._load()

    def _load(self):
        """加载状态"""
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file) as f:
                    self.tasks = json.load(f)
            except Exception:
                self.tasks = {}

    def _save(self):
        """保存状态"""
        try:
            with open(self.state_file, "w") as f:
                json.dump(self.tasks, f, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"状态保存失败: {e}")

    def create(self, inquiry: InquiryContext) -> str:
        """创建任务"""
        task_id = f"TASK-{datetime.now().strftime('%Y%m%d%H%M%S')}-{inquiry.inquiry_id[:8]}"

        self.tasks[task_id] = {
            "task_id": task_id,
            "inquiry": {
                "inquiry_id": inquiry.inquiry_id,
                "buyer_id": inquiry.buyer_id,
                "buyer_name": inquiry.buyer_name,
                "product_name": inquiry.product_name,
                "product_category": inquiry.product_category,
                "inquiry_content": inquiry.inquiry_content,
                "inquiry_type": inquiry.inquiry_type,
                "timestamp": inquiry.timestamp,
            },
            "status": "created",
            "expert_responses": [],
            "aggregated_result": None,
            "final_reply": None,
            "send_result": None,
            "created_at": datetime.now().isoformat(),
            "completed_at": None,
            "error": None
        }

        self._save()
        logger.info(f"创建任务: {task_id}")
        return task_id

    def update_status(self, task_id: str, status: str, **kwargs):
        """更新任务状态"""
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = status
            for k, v in kwargs.items():
                self.tasks[task_id][k] = v
            self._save()

    def get(self, task_id: str) -> Optional[dict]:
        """获取任务"""
        return self.tasks.get(task_id)

    def mark_complete(self, task_id: str, result: dict):
        """标记任务完成"""
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = "completed"
            self.tasks[task_id]["completed_at"] = datetime.now().isoformat()
            self.tasks[task_id]["final_reply"] = result.get("final_reply")
            self.tasks[task_id]["send_result"] = result.get("send_result")
            self._save()

    def mark_error(self, task_id: str, error: str):
        """标记任务错误"""
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = "error"
            self.tasks[task_id]["error"] = error
            self.tasks[task_id]["completed_at"] = datetime.now().isoformat()
            self._save()


# ==================== 主处理流程 ====================

class InquiryProcessor:
    """询盘处理器 - 协调整个流程"""

    def __init__(self):
        self.tracker = TaskTracker()

    async def process(
        self,
        message_id: str,
        buyer_id: str,
        buyer_name: str,
        product_name: str,
        content: str,
        raw: dict = None
    ) -> dict:
        """
        处理询盘的完整流程

        流程：
        1. 解析消息 → InquiryContext
        2. 分类 → routing
        3. 并行调用对应 Expert
        4. 汇总到 Aggregator
        5. 发送至 Exit Agent
        6. 返回结果
        """

        # 1. 创建上下文
        timestamp = datetime.now().isoformat()
        inquiry = InquiryContext(
            inquiry_id=message_id,
            buyer_id=buyer_id,
            buyer_name=buyer_name,
            product_name=product_name,
            product_category="",
            inquiry_content=content,
            inquiry_type="other",
            timestamp=timestamp
        )

        # 2. 分类
        inquiry.inquiry_type = InquiryClassifier.classify(content, product_name)
        logger.info(f"询盘分类: {inquiry.inquiry_type}")

        # 3. 创建任务
        task_id = self.tracker.create(inquiry)
        inquiry_id = inquiry.inquiry_id

        try:
            # 4. 调用 Expert（根据类型）
            expert_result = await self._call_expert(inquiry)

            # 5. 聚合
            aggregated = await self._aggregate(task_id, inquiry, expert_result)

            # 6. 发送
            send_result = await self._send_to_exit(task_id, inquiry, aggregated)

            # 7. 完成
            final_result = {
                "final_reply": aggregated.get("final_reply", ""),
                "reply_type": aggregated.get("reply_type", "pending"),
                "confidence": aggregated.get("confidence", 0.0),
                "send_result": send_result
            }

            self.tracker.mark_complete(task_id, final_result)

            return {
                "success": True,
                "inquiry_id": inquiry_id,
                "task_id": task_id,
                "reply_type": aggregated.get("reply_type", "pending"),
                "confidence": aggregated.get("confidence", 0.0),
                "final_reply": aggregated.get("final_reply", ""),
                "send_result": send_result
            }

        except Exception as e:
            logger.error(f"处理失败: {e}")
            self.tracker.mark_error(task_id, str(e))

            # 尝试 Hermes 兜底
            return await self._hermes_fallback(inquiry)

    async def _call_expert(self, inquiry: InquiryContext) -> dict:
        """调用对应的 Expert Agent"""

        expert_map = {
            "price": "classifier",
            "sample": "classifier",
            "customization": "classifier",
            "other": "classifier"
        }

        expert_name = expert_map.get(inquiry.inquiry_type, "classifier")

        try:
            # 调用 Classifier/Expert
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{A2A_ENDPOINTS[expert_name]}/process",
                    json={
                        "inquiry_content": inquiry.inquiry_content,
                        "buyer_name": inquiry.buyer_name,
                        "product_name": inquiry.product_name,
                        "context": {
                            "inquiry_id": inquiry.inquiry_id,
                            "buyer_id": inquiry.buyer_id,
                            "inquiry_type": inquiry.inquiry_type
                        }
                    },
                    timeout=aiohttp.ClientTimeout(total=45)
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info(f"Expert 响应: {expert_name} -> confidence={result.get('confidence', 0)}")
                        return result
                    else:
                        logger.warning(f"Expert 调用失败: {resp.status}")
                        return {"success": False, "error": f"HTTP {resp.status}"}

        except asyncio.TimeoutError:
            logger.error(f"Expert 调用超时: {expert_name}")
            return {"success": False, "error": "timeout"}
        except Exception as e:
            logger.error(f"Expert 调用异常: {e}")
            return {"success": False, "error": str(e)}

    async def _aggregate(
        self,
        task_id: str,
        inquiry: InquiryContext,
        expert_result: dict
    ) -> dict:
        """汇总到 Aggregator"""

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{A2A_ENDPOINTS['aggregator']}/aggregate",
                    json={
                        "inquiry_id": inquiry.inquiry_id,
                        "buyer_id": inquiry.buyer_id,
                        "buyer_name": inquiry.buyer_name,
                        "product_name": inquiry.product_name,
                        "product_category": inquiry.product_category,
                        "inquiry_content": inquiry.inquiry_content,
                        "inquiry_type": inquiry.inquiry_type,
                        "timestamp": inquiry.timestamp,
                        "expert_responses": [expert_result]
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        result = await resp.json()
                        logger.info(f"Aggregator -> reply_type={result.get('reply_type')}")
                        return result
                    else:
                        return {"success": False, "error": f"HTTP {resp.status}"}

        except Exception as e:
            logger.error(f"Aggregator 调用失败: {e}")
            return {"success": False, "error": str(e)}

    async def _send_to_exit(
        self,
        task_id: str,
        inquiry: InquiryContext,
        aggregated: dict
    ) -> dict:
        """发送到 Exit Agent"""

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{A2A_ENDPOINTS['exit']}/process",
                    json={
                        "inquiry_id": inquiry.inquiry_id,
                        "buyer_id": inquiry.buyer_id,
                        "buyer_name": inquiry.buyer_name,
                        "product_name": inquiry.product_name,
                        "inquiry_content": inquiry.inquiry_content,
                        "final_reply": aggregated.get("final_reply", ""),
                        "inquiry_type": inquiry.inquiry_type,
                        "confidence": aggregated.get("confidence", 0.0),
                        "requires_human": aggregated.get("requires_human", True),
                        "risk_level": aggregated.get("risk_level", "medium"),
                        "risk_flags": aggregated.get("risk_flags", [])
                    },
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    else:
                        return {"success": False, "error": f"HTTP {resp.status}"}

        except Exception as e:
            logger.error(f"Exit 调用失败: {e}")
            return {"success": False, "error": str(e)}

    async def _hermes_fallback(self, inquiry: InquiryContext) -> dict:
        """
        Hermes 兜底处理
        当多智能体流程失败时，使用 Hermes AI 直接生成回复
        """

        logger.info("启用 Hermes 兜底处理")

        result = await hermes_client.generate_reply(
            inquiry_type=inquiry.inquiry_type,
            buyer_name=inquiry.buyer_name,
            product_name=inquiry.product_name,
            inquiry_content=inquiry.inquiry_content
        )

        if result.success:
            return {
                "success": True,
                "inquiry_id": inquiry.inquiry_id,
                "task_id": "hermes-fallback",
                "reply_type": "human_review",  # Hermes 生成仍需审核
                "confidence": 0.5,
                "final_reply": result.reply,
                "send_result": None,
                "fallback": True
            }
        else:
            return {
                "success": False,
                "inquiry_id": inquiry.inquiry_id,
                "task_id": "hermes-fallback",
                "reply_type": "pending",
                "confidence": 0.0,
                "final_reply": f"抱歉，系统处理失败。请联系客服人工处理。",
                "error": result.error
            }


# ==================== FastAPI 接口 ====================

processor = InquiryProcessor()
tracker = TaskTracker()


@app.post("/1688/webhook", response_model=InquiryResponse)
async def handle_1688_webhook(request: Request):
    """
    接收 1688 开放平台的 webhook 回调

    1688 会 POST 询盘消息到这个端点
    """

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 解析消息
    message_id = body.get("messageId", body.get("message_id", ""))
    buyer_id = body.get("buyerId", body.get("buyer_id", ""))
    buyer_name = body.get("buyerName", body.get("buyer_name", "匿名买家"))
    product_name = body.get("productName", body.get("product_name", ""))
    content = body.get("content", body.get("text", ""))

    if not content:
        raise HTTPException(status_code=400, detail="Missing content")

    logger.info(f"收到询盘: {message_id} from {buyer_name}")

    # 处理
    result = await processor.process(
        message_id=message_id,
        buyer_id=buyer_id,
        buyer_name=buyer_name,
        product_name=product_name,
        content=content,
        raw=body
    )

    return InquiryResponse(
        success=result["success"],
        inquiry_id=result["inquiry_id"],
        reply_type=result.get("reply_type", "pending"),
        confidence=result.get("confidence", 0.0),
        reply_preview=result.get("final_reply", "")[:100],
        message="处理完成" if result["success"] else "处理失败"
    )


@app.get("/1688/test")
async def test_endpoint():
    """测试端点"""
    return {
        "status": "ok",
        "service": "Entry Agent - 1688 Gateway",
        "endpoints": {
            "webhook": "POST /1688/webhook",
            "health": "GET /health",
            "tasks": "GET /tasks",
            "task": "GET /tasks/{task_id}",
            "hermes": "GET /hermes/test"
        }
    }


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "entry"}


@app.get("/tasks")
async def list_tasks(limit: int = 20):
    """列出最近的任务"""
    tasks = list(tracker.tasks.items())[-limit:]
    return {
        "count": len(tasks),
        "tasks": [
            {
                "task_id": k,
                "status": v.get("status"),
                "buyer_name": v.get("inquiry", {}).get("buyer_name"),
                "inquiry_type": v.get("inquiry", {}).get("inquiry_type"),
                "created_at": v.get("created_at"),
                "completed_at": v.get("completed_at")
            }
            for k, v in tasks
        ]
    }


@app.get("/tasks/{task_id}")
async def get_task(task_id: str):
    """获取任务详情"""
    task = tracker.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@app.get("/hermes/test")
async def test_hermes():
    """测试 Hermes 连接"""
    result = await hermes_client.chat(
        message="你好，请回复'连接正常'",
        system_prompt="你是一个测试助手"
    )
    return {
        "success": result.success,
        "reply": result.reply if result.success else None,
        "error": result.error if not result.success else None
    }


@app.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card"""
    return {
        "name": "1688 Entry Agent",
        "description": "1688询盘入口，接收 webhook，分类路由，协调多智能体处理",
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "supportedTasks": ["inquiry_receive", "route", "coordinate"]
        },
        "endpoint": "http://entry-agent:8001",
        "skills": ["inquiry_parsing", "routing", "orchestration"]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)

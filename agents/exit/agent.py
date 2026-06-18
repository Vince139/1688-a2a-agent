"""
Exit Agent - 出口
负责最终发送决策：自动发送回复 or 推送到人工审核队列
并通过企业微信等渠道通知相关人员
"""

import os
import json
import logging
from typing import Optional, Any
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

import yaml
import aiohttp
from fastapi import FastAPI
from pydantic import BaseModel

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.a2a_client import A2AClientManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Exit Agent")

# ==================== 配置 ====================
with open(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "config.yaml")) as f:
    config = yaml.safe_load(f)

A2A_ENDPOINTS = config["a2a"]["agents"]
NOTIFICATION_CONFIG = config.get("notification", {})
REPLY_CONFIG = config.get("reply", {})

a2a_client = A2AClientManager()
for name, url in A2A_ENDPOINTS.items():
    a2a_client.register(name, url)


# ==================== 数据模型 ====================

class SendResult(str, Enum):
    AUTO_SENT = "auto_sent"       # 自动发送成功
    AUTO_FAILED = "auto_failed"   # 自动发送失败
    QUEUED_HUMAN = "queued_human" # 已加入人工队列
    SKIPPED = "skipped"           # 跳过（异常情况）


@dataclass
class SendDecision:
    """发送决策"""
    action: str  # "auto_send" | "human_queue" | "skip"
    reason: str
    urgency: str  # "high" | "normal" | "low"


@dataclass
class SendResultData:
    """发送结果"""
    success: bool
    result: SendResult
    message: str
    inquiry_id: str
    reply_preview: str  # 回复内容预览（截取前100字）
    sent_at: str = field(default_factory=lambda: datetime.now().isoformat())


# ==================== 1688 发送客户端 ====================

class AlibabaClient:
    """1688 API 客户端"""

    def __init__(self):
        aliyun = config.get("aliyun", {})
        self.app_key = aliyun.get("app_key", "")
        self.app_secret = aliyun.get("app_secret", "")
        self.refresh_token = aliyun.get("refresh_token", "")

    async def reply_message(self, message_id: str, content: str) -> bool:
        """发送回复消息"""
        # 实际接入时使用 top-sdk
        # 这里模拟发送逻辑

        if not self.app_key or self.app_key == "your_app_key":
            logger.warning("1688 API 未配置，模拟发送")
            return True

        try:
            # 实际 API 调用示例
            # from top_sdk import TopClient
            # client = TopClient(appkey=self.app_key, appsecret=self.app_secret)
            # result = client.execute(
            #     'alibaba.icos.message.reply',
            #     {'messageId': message_id, 'content': content}
            # )
            # return result.get('success', False)

            logger.info(f"模拟发送回复到 1688 message_id={message_id}")
            logger.info(f"回复内容：{content[:100]}...")
            return True

        except Exception as e:
            logger.error(f"1688 发送失败: {e}")
            return False


# ==================== 人工队列管理器 ====================

class HumanQueueManager:
    """人工审核队列管理器"""

    def __init__(self):
        self.queue_file = "/tmp/1688_human_queue.json"
        self._ensure_queue_file()

    def _ensure_queue_file(self):
        """确保队列文件存在"""
        if not os.path.exists(self.queue_file):
            with open(self.queue_file, "w") as f:
                json.dump([], f)

    def add(
        self,
        inquiry_id: str,
        buyer_name: str,
        product_name: str,
        inquiry_content: str,
        final_reply: str,
        risk_flags: list,
        confidence: float
    ) -> str:
        """添加任务到人工队列，返回队列ID"""
        queue_id = f"HR-{datetime.now().strftime('%Y%m%d%H%M%S')}-{inquiry_id[:8]}"

        entry = {
            "queue_id": queue_id,
            "inquiry_id": inquiry_id,
            "buyer_name": buyer_name,
            "product_name": product_name,
            "inquiry_content": inquiry_content,
            "suggested_reply": final_reply,
            "risk_flags": risk_flags,
            "confidence": confidence,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
            "assigned_to": None,
            "processed_at": None
        }

        with open(self.queue_file, "r") as f:
            queue = json.load(f)

        queue.append(entry)

        with open(self.queue_file, "w") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)

        logger.info(f"添加人工队列: {queue_id}")
        return queue_id

    def get_pending(self, limit: int = 20) -> list:
        """获取待处理队列"""
        with open(self.queue_file, "r") as f:
            queue = json.load(f)

        pending = [item for item in queue if item["status"] == "pending"]
        return pending[:limit]

    def mark_processed(self, queue_id: str, status: str = "processed", assigned_to: str = None):
        """标记任务已处理"""
        with open(self.queue_file, "r") as f:
            queue = json.load(f)

        for item in queue:
            if item["queue_id"] == queue_id:
                item["status"] = status
                item["processed_at"] = datetime.now().isoformat()
                if assigned_to:
                    item["assigned_to"] = assigned_to
                break

        with open(self.queue_file, "w") as f:
            json.dump(queue, f, ensure_ascii=False, indent=2)

    def get_stats(self) -> dict:
        """获取队列统计"""
        with open(self.queue_file, "r") as f:
            queue = json.load(f)

        pending = [item for item in queue if item["status"] == "pending"]
        processed = [item for item in queue if item["status"] == "processed"]

        return {
            "total": len(queue),
            "pending": len(pending),
            "processed": len(processed),
            "recent": queue[-5:] if len(queue) >= 5 else queue
        }


# ==================== 通知服务 ====================

class NotificationService:
    """通知服务"""

    def __init__(self):
        self.wecom_webhook = NOTIFICATION_CONFIG.get("wecom_webhook")

    async def notify_new_human_task(self, queue_entry: dict):
        """通知新的人工任务"""
        if not self.wecom_webhook:
            logger.warning("企业微信 webhook 未配置")
            return

        risk_emoji = "🔴" if queue_entry.get("risk_flags") else "🟡"
        msg = f"""{'='*30}
{risk_emoji} 1688 新人工审核任务
{'='*30}

🆔 队列ID：{queue_entry['queue_id']}
👤 买家：{queue_entry['buyer_name']}
📦 商品：{queue_entry['product_name']}
📊 置信度：{queue_entry['confidence']:.0%}
⚠️ 风险标记：{', '.join(queue_entry['risk_flags']) if queue_entry['risk_flags'] else '无'}

📝 询盘内容：
{queue_entry['inquiry_content'][:150]}...

💬 建议回复：
{queue_entry['suggested_reply'][:200]}...
"""

        await self._send_wecom(msg)

    async def notify_auto_reply_failed(self, inquiry_id: str, error: str):
        """通知自动回复失败"""
        if not self.wecom_webhook:
            return

        msg = f"""⚠️ 1688 自动回复失败

🆔 询盘ID：{inquiry_id}
❌ 错误：{error}

请人工处理！
"""

        await self._send_wecom(msg)

    async def notify_queue_alert(self, pending_count: int):
        """人工队列告警（超过阈值）"""
        threshold = NOTIFICATION_CONFIG.get("human_queue_alert", 5)

        if pending_count >= threshold:
            if not self.wecom_webhook:
                return

            msg = f"""🚨 1688 人工队列告警

当前待处理任务：{pending_count} 条
告警阈值：{threshold} 条

请及时处理！
"""

            await self._send_wecom(msg)

    async def notify_auto_reply_success(self, inquiry_id: str, buyer_name: str):
        """通知自动回复成功（可选，用于监控）"""
        # 通常不需要通知成功，只有失败才通知
        pass

    async def _send_wecom(self, content: str) -> bool:
        """发送企业微信消息"""
        if not self.wecom_webhook:
            return False

        payload = {
            "msgtype": "text",
            "text": {
                "content": content
            }
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self.wecom_webhook,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    success = resp.status == 200
                    if success:
                        logger.info("企业微信通知发送成功")
                    else:
                        logger.warning(f"企业微信通知发送失败: {resp.status}")
                    return success
        except Exception as e:
            logger.error(f"企业微信通知发送异常: {e}")
            return False


# ==================== 发送决策器 ====================

class SendDecisionMaker:
    """发送决策器"""

    def __init__(self):
        self.auto_reply_types = REPLY_CONFIG.get("auto_reply_types", ["price", "sample", "logistics", "quality"])
        self.high_risk_keywords = REPLY_CONFIG.get("high_risk_keywords", [])

    def decide(
        self,
        inquiry_type: str,
        confidence: float,
        requires_human: bool,
        risk_level: str,
        risk_flags: list
    ) -> SendDecision:
        """
        决定发送策略

        决策树：
        1. requires_human = True → 人工队列
        2. risk_level = HIGH → 人工队列
        3. risk_flags 包含高风险词 → 人工队列
        4. inquiry_type 不在自动范围 → 人工队列
        5. confidence < 阈值 → 人工队列
        6. 否则 → 自动发送
        """

        # 检查项
        if requires_human:
            return SendDecision(
                action="human_queue",
                reason="专家标记需要人工处理",
                urgency="normal"
            )

        if risk_level == "high":
            return SendDecision(
                action="human_queue",
                reason=f"风险等级高：{', '.join(risk_flags[:2])}",
                urgency="high"
            )

        if any(kw in str(risk_flags) for kw in self.high_risk_keywords):
            return SendDecision(
                action="human_queue",
                reason="高风险关键词命中",
                urgency="high"
            )

        if inquiry_type not in self.auto_reply_types:
            return SendDecision(
                action="human_queue",
                reason=f"询盘类型 {inquiry_type} 不在自动回复范围",
                urgency="normal"
            )

        threshold = REPLY_CONFIG.get("auto_reply_threshold", 85)
        if confidence * 100 < threshold:
            return SendDecision(
                action="human_queue",
                reason=f"置信度 {confidence*100:.0f}% < 阈值 {threshold}%",
                urgency="normal"
            )

        return SendDecision(
            action="auto_send",
            reason="满足自动发送条件",
            urgency="normal"
        )


# ==================== Exit Agent ====================

class ExitAgent:
    """出口 Agent"""

    def __init__(self):
        self.alibaba = AlibabaClient()
        self.queue_manager = HumanQueueManager()
        self.notification = NotificationService()
        self.decision_maker = SendDecisionMaker()

    async def process(
        self,
        inquiry_id: str,
        buyer_id: str,
        buyer_name: str,
        product_name: str,
        inquiry_content: str,
        final_reply: str,
        inquiry_type: str,
        confidence: float,
        requires_human: bool,
        risk_level: str,
        risk_flags: list
    ) -> SendResultData:
        """
        处理最终发送
        """

        logger.info(f"Exit 处理询盘: {inquiry_id}")

        # 1. 决策
        decision = self.decision_maker.decide(
            inquiry_type=inquiry_type,
            confidence=confidence,
            requires_human=requires_human,
            risk_level=risk_level,
            risk_flags=risk_flags
        )

        logger.info(f"发送决策: {decision.action} - {decision.reason}")

        # 2. 执行决策
        if decision.action == "auto_send":
            return await self._auto_send(
                inquiry_id=inquiry_id,
                final_reply=final_reply
            )

        elif decision.action == "human_queue":
            return await self._queue_for_human(
                inquiry_id=inquiry_id,
                buyer_name=buyer_name,
                product_name=product_name,
                inquiry_content=inquiry_content,
                final_reply=final_reply,
                risk_flags=risk_flags,
                confidence=confidence,
                urgency=decision.urgency
            )

        else:
            return SendResultData(
                success=False,
                result=SendResult.SKIPPED,
                message=f"未知决策类型: {decision.action}",
                inquiry_id=inquiry_id,
                reply_preview=final_reply[:100]
            )

    async def _auto_send(self, inquiry_id: str, final_reply: str) -> SendResultData:
        """自动发送"""
        try:
            success = await self.alibaba.reply_message(inquiry_id, final_reply)

            if success:
                logger.info(f"自动发送成功: {inquiry_id}")
                return SendResultData(
                    success=True,
                    result=SendResult.AUTO_SENT,
                    message="回复已自动发送",
                    inquiry_id=inquiry_id,
                    reply_preview=final_reply[:100]
                )
            else:
                logger.error(f"自动发送失败: {inquiry_id}")
                # 发送失败，加入人工队列
                await self.notification.notify_auto_reply_failed(
                    inquiry_id, "1688 API 返回失败"
                )
                return SendResultData(
                    success=False,
                    result=SendResult.AUTO_FAILED,
                    message="1688 API 发送失败，已通知人工处理",
                    inquiry_id=inquiry_id,
                    reply_preview=final_reply[:100]
                )

        except Exception as e:
            logger.error(f"自动发送异常: {e}")
            return SendResultData(
                success=False,
                result=SendResult.AUTO_FAILED,
                message=f"发送异常: {str(e)}",
                inquiry_id=inquiry_id,
                reply_preview=final_reply[:100]
            )

    async def _queue_for_human(
        self,
        inquiry_id: str,
        buyer_name: str,
        product_name: str,
        inquiry_content: str,
        final_reply: str,
        risk_flags: list,
        confidence: float,
        urgency: str
    ) -> SendResultData:
        """加入人工队列"""
        queue_id = self.queue_manager.add(
            inquiry_id=inquiry_id,
            buyer_name=buyer_name,
            product_name=product_name,
            inquiry_content=inquiry_content,
            final_reply=final_reply,
            risk_flags=risk_flags,
            confidence=confidence
        )

        # 获取队列条目用于通知
        pending = self.queue_manager.get_pending(limit=1)
        if pending:
            await self.notification.notify_new_human_task(pending[-1])

        # 检查是否需要告警
        stats = self.queue_manager.get_stats()
        if stats["pending"] >= NOTIFICATION_CONFIG.get("human_queue_alert", 5):
            await self.notification.notify_queue_alert(stats["pending"])

        return SendResultData(
            success=True,
            result=SendResult.QUEUED_HUMAN,
            message=f"已加入人工队列: {queue_id}",
            inquiry_id=inquiry_id,
            reply_preview=final_reply[:100]
        )


# ==================== FastAPI 接口 ====================

exit_agent = ExitAgent()


class ProcessRequest(BaseModel):
    inquiry_id: str
    buyer_id: str = ""
    buyer_name: str
    product_name: str
    inquiry_content: str
    final_reply: str
    inquiry_type: str
    confidence: float
    requires_human: bool
    risk_level: str
    risk_flags: list = []


@app.post("/process")
async def process_request(request: ProcessRequest):
    """A2A 接口：处理发送"""
    logger.info(f"Exit 处理请求: {request.inquiry_id}")

    result = await exit_agent.process(
        inquiry_id=request.inquiry_id,
        buyer_id=request.buyer_id,
        buyer_name=request.buyer_name,
        product_name=request.product_name,
        inquiry_content=request.inquiry_content,
        final_reply=request.final_reply,
        inquiry_type=request.inquiry_type,
        confidence=request.confidence,
        requires_human=request.requires_human,
        risk_level=request.risk_level,
        risk_flags=request.risk_flags
    )

    return {
        "success": result.success,
        "result": result.result.value,
        "message": result.message,
        "inquiry_id": result.inquiry_id,
        "reply_preview": result.reply_preview,
        "sent_at": result.sent_at
    }


@app.get("/queue")
async def get_queue():
    """获取人工队列"""
    pending = exit_agent.queue_manager.get_pending()
    return {
        "pending_count": len(pending),
        "items": pending
    }


@app.get("/queue/stats")
async def get_queue_stats():
    """获取队列统计"""
    return exit_agent.queue_manager.get_stats()


@app.post("/queue/{queue_id}/approve")
async def approve_reply(queue_id: str):
    """批准发送（人工审核后）"""
    entry = None
    for item in exit_agent.queue_manager.get_pending(limit=100):
        if item["queue_id"] == queue_id:
            entry = item
            break

    if not entry:
        return {"success": False, "message": "队列项不存在"}

    # 发送
    success = await exit_agent.alibaba.reply_message(
        entry["inquiry_id"],
        entry["suggested_reply"]
    )

    if success:
        exit_agent.queue_manager.mark_processed(queue_id, status="approved")
        return {"success": True, "message": "已发送"}
    else:
        return {"success": False, "message": "发送失败"}


@app.post("/queue/{queue_id}/reject")
async def reject_reply(queue_id: str, reason: str = ""):
    """拒绝并标记（人工审核后）"""
    exit_agent.queue_manager.mark_processed(queue_id, status="rejected")
    return {"success": True, "message": f"已拒绝: {reason}"}


@app.post("/queue/{queue_id}/edit")
async def edit_and_send(queue_id: str, edited_reply: str):
    """编辑后发送"""
    entry = None
    for item in exit_agent.queue_manager.get_pending(limit=100):
        if item["queue_id"] == queue_id:
            entry = item
            break

    if not entry:
        return {"success": False, "message": "队列项不存在"}

    success = await exit_agent.alibaba.reply_message(
        entry["inquiry_id"],
        edited_reply
    )

    if success:
        exit_agent.queue_manager.mark_processed(queue_id, status="edited")
        return {"success": True, "message": "已发送修改后的回复"}
    else:
        return {"success": False, "message": "发送失败"}


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "exit"}


@app.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card"""
    return {
        "name": "1688 Exit Agent",
        "description": "出口Agent，负责最终发送决策：自动发送或推入人工队列",
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": True,
            "supportedTasks": ["send", "human_queue", "notification"]
        },
        "endpoint": "http://exit-agent:8007",
        "skills": ["send_decision", "queue_management", "notification"]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)

"""
Aggregator Agent - 聚合器
负责汇总各 Expert Agent 的回复，进行质量检查，生成最终输出，判断是否需要人工审核
"""

import os
import re
import json
import logging
from typing import Optional, Any
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime

import yaml
import aiohttp
from fastapi import FastAPI
from pydantic import BaseModel

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.a2a_client import A2AClientManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Aggregator Agent")

# ==================== 配置 ====================
with open(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "config.yaml")) as f:
    config = yaml.safe_load(f)

A2A_ENDPOINTS = config["a2a"]["agents"]
REPLY_CONFIG = config.get("reply", {})
AI_CONFIG = config.get("ai", {})
NOTIFICATION_CONFIG = config.get("notification", {})

a2a_client = A2AClientManager()
for name, url in A2A_ENDPOINTS.items():
    a2a_client.register(name, url)


# ==================== 数据模型 ====================

class InquiryType(str, Enum):
    PRICE = "price"
    SAMPLE = "sample"
    CUSTOMIZATION = "customization"
    LOGISTICS = "logistics"
    QUALITY = "quality"
    OTHER = "other"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class ExpertResponse:
    """专家回复"""
    expert_name: str  # "price_expert" | "sample_expert" | "customize_expert"
    success: bool
    reply: str
    confidence: float
    requires_human: bool
    raw_data: dict = None
    product_info: dict = None
    assessment: dict = None


@dataclass
class InquiryContext:
    """完整询盘上下文"""
    inquiry_id: str
    buyer_id: str
    buyer_name: str
    product_name: str
    product_category: str
    inquiry_content: str
    inquiry_type: InquiryType
    timestamp: str
    trace: list = field(default_factory=list)


@dataclass
class AggregatedResult:
    """聚合结果"""
    success: bool
    final_reply: str
    reply_type: str  # "auto" | "human_review" | "generic"
    confidence: float
    risk_level: RiskLevel
    requires_human: bool
    risk_flags: list[str]
    expert_responses: list[ExpertResponse]
    suggestions: list[str]  # 改进建议


# ==================== 质量检查 ====================

class ReplyQualityChecker:
    """回复质量检查器"""

    # 回复质量评分维度
    DIMENSIONS = [
        "completeness",   # 完整性
        "accuracy",       # 准确性
        "professionalism", # 专业性
        "actionability",  # 可操作性
        "tone",           # 语气
    ]

    # 各维度权重
    WEIGHTS = {
        "completeness": 0.2,
        "accuracy": 0.3,
        "professionalism": 0.2,
        "actionability": 0.2,
        "tone": 0.1,
    }

    @classmethod
    def check(cls, reply: str, expert_responses: list[ExpertResponse], context: InquiryContext) -> dict:
        """
        检查回复质量，返回各维度评分和改进建议
        """
        scores = {}
        suggestions = []

        # 1. 完整性检查
        completeness = cls._check_completeness(reply, context)
        scores["completeness"] = completeness
        if completeness < 0.7:
            suggestions.append("回复内容不够完整，建议补充产品信息或操作指引")

        # 2. 准确性检查
        accuracy = cls._check_accuracy(reply, expert_responses)
        scores["accuracy"] = accuracy
        if accuracy < 0.6:
            suggestions.append("回复内容可能存在信息不一致，请核实")

        # 3. 专业性检查
        professionalism = cls._check_professionalism(reply)
        scores["professionalism"] = professionalism
        if professionalism < 0.5:
            suggestions.append("建议使用更专业的商业用语")

        # 4. 可操作性检查
        actionability = cls._check_actionability(reply)
        scores["actionability"] = actionability
        if actionability < 0.6:
            suggestions.append("回复缺少明确的行动指引，建议添加下一步操作说明")

        # 5. 语气检查
        tone = cls._check_tone(reply)
        scores["tone"] = tone
        if tone < 0.6:
            suggestions.append("语气可以更热情专业，增加亲和力")

        # 综合评分
        weighted_score = sum(scores[d] * cls.WEIGHTS[d] for d in cls.DIMENSIONS)

        return {
            "scores": scores,
            "weighted_score": weighted_score,
            "suggestions": suggestions
        }

    @staticmethod
    def _check_completeness(reply: str, context: InquiryContext) -> float:
        """检查完整性"""
        score = 0.5  # 基础分

        # 检查基本要素
        has_greeting = any(g in reply for g in ["您好", "您好！", "尊敬的"])
        has_product = context.product_name and context.product_name in reply
        has_cta = any(kw in reply for kw in ["下一步", "联系", "微信", "加我"])
        has_closing = any(kw in reply for kw in ["期待", "合作", "祝您", "谢谢"])

        if has_greeting: score += 0.1
        if has_product or len(reply) > 100: score += 0.15
        if has_cta: score += 0.15
        if has_closing: score += 0.1

        return min(score, 1.0)

    @staticmethod
    def _check_accuracy(reply: str, expert_responses: list[ExpertResponse]) -> float:
        """检查准确性"""
        score = 0.5

        # 检查是否有专家回复成功
        successful = [r for r in expert_responses if r.success]
        if not successful:
            return 0.3

        # 检查回复内容是否有明显错误
        error_patterns = [
            r"抱歉.*系统错误",
            r"无法.*处理",
            r"请联系.*不支持",
        ]

        has_error = any(re.search(p, reply) for p in error_patterns)
        if has_error:
            score -= 0.2

        # 检查回复长度是否合理
        if 50 < len(reply) < 2000:
            score += 0.2

        # 检查置信度一致性
        avg_confidence = sum(r.confidence for r in successful) / len(successful)
        if avg_confidence > 0.8:
            score += 0.15

        return max(min(score, 1.0), 0.0)

    @staticmethod
    def _check_professionalism(reply: str) -> float:
        """检查专业性"""
        score = 0.5

        # 专业词汇
        professional_words = [
            "起订量", "MOQ", "交期", "样品费", "定制",
            "报价", "认证", "规格", "包装", "发货",
            "源头工厂", "品质保障", "OEM", "ODM"
        ]

        found_words = sum(1 for w in professional_words if w in reply)
        score += min(found_words * 0.05, 0.3)

        # 非专业词汇
        unprofessional_patterns = [
            r"亲[亲呀啊]",  # 过度亲昵
            r"哦$",  # 口语化结尾
            r"便宜点",  # 不专业
        ]

        unprofessional = any(re.search(p, reply) for p in unprofessional_patterns)
        if unprofessional:
            score -= 0.15

        return max(min(score, 1.0), 0.0)

    @staticmethod
    def _check_actionability(reply: str) -> float:
        """检查可操作性"""
        score = 0.4

        # 行动指引
        action_items = [
            (r"加[微我]信", "提供微信联系方式"),
            (r"点击", "提供点击操作"),
            (r"联系", "提供联系方式"),
            (r"\d+小时", "明确响应时间"),
            (r"回复", "要求买家回复"),
            (r"确认", "要求确认信息"),
        ]

        for pattern, desc in action_items:
            if re.search(pattern, reply):
                score += 0.1

        return min(score, 1.0)

    @staticmethod
    def _check_tone(reply: str) -> float:
        """检查语气"""
        score = 0.6

        # 正面语气词
        positive_tone = ["感谢", "欢迎", "期待", "专业", "品质"]
        negative_tone = ["抱歉", "对不起", "不好意思", "麻烦", "抱歉"]

        positive_count = sum(1 for t in positive_tone if t in reply)
        negative_count = sum(1 for t in negative_tone if t in reply)

        if positive_count >= 2:
            score += 0.15
        elif positive_count == 0:
            score -= 0.1

        if negative_count > 2:
            score -= 0.15

        # 表情符号（适度使用）
        emoji_count = len(re.findall(r'[\U0001F300-\U0001F9FF]', reply))
        if 1 <= emoji_count <= 5:
            score += 0.1
        elif emoji_count > 8:
            score -= 0.1

        return max(min(score, 1.0), 0.0)


# ==================== 风险评估 ====================

class RiskAssessor:
    """风险评估器"""

    HIGH_RISK_KEYWORDS = [
        "仿牌", "高仿", "A货", "假货", "假",
        "账期", "月结", "赊账", "先货后款", "回扣",
        "走私", "低报", "逃税",
    ]

    MEDIUM_RISK_KEYWORDS = [
        "定制", "贴牌", "OEM", "ODM",
        "品牌授权", "授权文件",
        "特殊规格", "非标",
    ]

    @classmethod
    def assess(
        cls,
        inquiry_content: str,
        expert_responses: list[ExpertResponse],
        context: InquiryContext
    ) -> tuple[RiskLevel, list[str]]:
        """
        评估风险等级和风险标志

        Returns:
            (risk_level, risk_flags)
        """
        risk_flags = []

        # 1. 检查询盘内容中的高风险词
        for kw in cls.HIGH_RISK_KEYWORDS:
            if kw in inquiry_content:
                risk_flags.append(f"高风险词命中：{kw}")

        # 2. 检查专家标记的风险
        for resp in expert_responses:
            if resp.requires_human:
                risk_flags.append(f"专家标记需人工：{resp.expert_name}")
            if resp.raw_data:
                # 检查商品信息是否有问题
                product_name = resp.raw_data.get("product", {}).get("name", "")
                if any(kw in product_name for kw in ["仿", "A货", "假"]):
                    risk_flags.append(f"可疑商品名称：{product_name}")

        # 3. 检查置信度
        successful = [r for r in expert_responses if r.success]
        if successful:
            low_confidence = [r for r in successful if r.confidence < 0.7]
            if len(low_confidence) >= 2:
                risk_flags.append("多个专家置信度低")
            elif low_confidence:
                risk_flags.append(f"专家置信度低：{low_confidence[0].expert_name}")

        # 4. 检查询盘类型
        if context.inquiry_type == InquiryType.CUSTOMIZATION:
            if "品牌" in inquiry_content or "授权" in inquiry_content:
                risk_flags.append("品牌定制可能涉及授权问题")
        elif context.inquiry_type == InquiryType.OTHER:
            risk_flags.append("无法明确分类的询盘")

        # 5. 检查买家异常
        if cls._has_buying_abnormalities(context):
            risk_flags.append("买家行为存在异常")

        # 计算风险等级
        high_risk_count = sum(1 for f in risk_flags if "高风险" in f or "专家标记" in f or "置信度低" in f)
        medium_risk_count = len(risk_flags) - high_risk_count

        if high_risk_count > 0 or len(risk_flags) >= 3:
            risk_level = RiskLevel.HIGH
        elif medium_risk_count >= 2 or "无法明确分类" in str(risk_flags):
            risk_level = RiskLevel.MEDIUM
        else:
            risk_level = RiskLevel.LOW

        return risk_level, risk_flags

    @staticmethod
    def _has_buying_abnormalities(context: InquiryContext) -> bool:
        """检查买家行为是否异常"""
        # 异常模式
        abnormal_patterns = [
            r"先发货.*后付款",  # 要求先货后款
            r"收货后再.*钱",    # 货到付款要求
            r"试用.*满意.*付款", # 要求验货后再付款
            r"低价.*[0-9]{3,}", # 异常低价要求
        ]

        content = context.inquiry_content
        return any(re.search(p, content) for p in abnormal_patterns)


# ==================== 回复整合 ====================

class ReplyIntegrator:
    """回复整合器"""

    @staticmethod
    def integrate(
        expert_responses: list[ExpertResponse],
        context: InquiryContext,
        quality_result: dict,
        risk_level: RiskLevel
    ) -> str:
        """
        整合多个专家回复，生成最终统一回复

        策略：
        1. 如果只有一个专家成功，直接使用
        2. 如果多个专家成功，根据类型选择主回复 + 补充其他信息
        3. 如果都失败，生成通用回复
        """

        successful = [r for r in expert_responses if r.success]

        if not successful:
            return ReplyIntegrator._generate_generic_reply(context)

        if len(successful) == 1:
            return successful[0].reply

        # 多个回复，取置信度最高的作为主回复
        primary = max(successful, key=lambda r: r.confidence)
        secondary = [r for r in successful if r != primary]

        # 构建整合回复
        parts = []

        # 主回复开头
        primary_reply = primary.reply

        # 如果次要回复有额外有用信息，补充进去
        additional_info = []

        for resp in secondary:
            # 从次要回复中提取额外信息
            extra = ReplyIntegrator._extract_additional_info(resp, context)
            if extra:
                additional_info.extend(extra)

        # 整合
        if additional_info:
            # 在主回复末尾添加补充信息
            parts.append(primary_reply)
            parts.append("")
            parts.append("---")
            parts.append("**💡 补充信息：**")
            for info in additional_info[:3]:  # 最多3条
                parts.append(info)
        else:
            parts.append(primary_reply)

        return "\n".join(parts)

    @staticmethod
    def _extract_additional_info(resp: ExpertResponse, context: InquiryContext) -> list[str]:
        """从次要专家回复中提取额外信息"""
        info = []

        if not resp.raw_data:
            return info

        # 如果有产品信息但不在主回复中
        if resp.product_info and "product" in resp.product_info:
            product = resp.product_info["product"]
            if "moq" in product and "MOQ" not in resp.reply:
                info.append(f"- {product.get('name', '产品')}：MOQ {product['moq']}")
            if "delivery_time" in product and "交期" not in resp.reply:
                info.append(f"- 交期：{product['delivery_time']}")

        return info

    @staticmethod
    def _generate_generic_reply(context: InquiryContext) -> str:
        """生成通用回复"""
        return (
            f"尊敬的 {context.buyer_name}，您好！\n\n"
            f"感谢您的询盘！\n\n"
            f"我们已经收到您的需求，会尽快为您处理。\n"
            f"为了给您提供更准确的方案，请稍等片刻，"
            f"我们的销售经理会第一时间联系您！\n\n"
            f"如有紧急需求，可直接联系我们。\n"
            f"期待与您合作，祝您生意兴隆！"
        )


# ==================== AI 优化 ====================

class AIOptimizer:
    """AI 回复优化器"""

    def __init__(self):
        self.ai_config = AI_CONFIG

    async def optimize(
        self,
        reply: str,
        context: InquiryContext,
        quality_scores: dict
    ) -> str:
        """
        使用 AI 优化回复质量

        根据质量检查结果，有针对性地优化薄弱维度
        """

        improvement_focus = []
        if quality_scores.get("scores", {}).get("completeness", 1) < 0.7:
            improvement_focus.append("完整性")
        if quality_scores.get("scores", {}).get("actionability", 1) < 0.7:
            improvement_focus.append("行动指引")
        if quality_scores.get("scores", {}).get("tone", 1) < 0.7:
            improvement_focus.append("语气亲和力")

        if not improvement_focus:
            # 质量已经很好，直接返回
            return reply

        focus_text = "、".join(improvement_focus)

        optimized = await self._call_ai(
            f"""你是一个1688平台外贸业务员，需要优化以下回复。

当前回复：
---
{reply}
---

询盘信息：
- 买家：{context.buyer_name}
- 商品：{context.product_name}
- 询盘内容：{context.inquiry_content}

需要加强的方面：{focus_text}

请优化回复，保持专业性和信息准确性，重点改善以上方面。
回复长度控制在200字以内。
直接返回优化后的回复，不需要解释。"""
        )

        return optimized if optimized else reply

    async def _call_ai(self, prompt: str) -> Optional[str]:
        """调用 AI"""
        provider = self.ai_config.get("provider", "hermes")
        if provider != "hermes":
            return None

        hermes_config = self.ai_config.get("hermes", {})
        api_url = hermes_config.get("api_url", "http://hermes:8000")
        api_key = hermes_config.get("api_key", "")

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{api_url}/chat",
                    json={"message": prompt, "api_key": api_key},
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("reply", "")
        except Exception as e:
            logger.warning(f"AI 优化调用失败: {e}")

        return None


# ==================== 决策引擎 ====================

class DecisionEngine:
    """决策引擎 - 决定最终发送策略"""

    AUTO_REPLY_THRESHOLD = REPLY_CONFIG.get("auto_reply_threshold", 85)
    AUTO_REPLY_TYPES = REPLY_CONFIG.get("auto_reply_types", ["price", "sample", "logistics", "quality"])

    @classmethod
    def should_auto_reply(
        cls,
        aggregated_result: AggregatedResult,
        context: InquiryContext
    ) -> tuple[bool, str]:
        """
        决定是否自动回复

        Returns:
            (should_auto_reply, reason)
        """

        # 规则1: 风险等级高 → 不自动
        if aggregated_result.risk_level == RiskLevel.HIGH:
            return False, f"风险等级高：{', '.join(aggregated_result.risk_flags)}"

        # 规则2: 任何专家标记 requires_human → 不自动
        if any(r.requires_human for r in aggregated_result.expert_responses):
            expert_names = [r.expert_name for r in aggregated_result.expert_responses if r.requires_human]
            return False, f"专家标记需人工：{', '.join(expert_names)}"

        # 规则3: 置信度低于阈值 → 不自动
        if aggregated_result.confidence * 100 < cls.AUTO_REPLY_THRESHOLD:
            return False, f"置信度 {aggregated_result.confidence*100:.0f}% < 阈值 {cls.AUTO_REPLY_THRESHOLD}%"

        # 规则4: 询盘类型不在自动回复范围 → 不自动
        if context.inquiry_type.value not in cls.AUTO_REPLY_TYPES:
            return False, f"询盘类型 {context.inquiry_type.value} 不在自动回复范围"

        # 规则5: 质量评分过低 → 不自动
        weighted_score = aggregated_result.confidence  # 用置信度作为质量代理
        if weighted_score < 0.6:
            return False, f"质量评分过低：{weighted_score:.2f}"

        return True, "满足所有自动回复条件"

    @classmethod
    def determine_reply_type(
        cls,
        aggregated_result: AggregatedResult,
        context: InquiryContext
    ) -> str:
        """
        决定回复类型
        - "auto": 自动发送
        - "human_review": 需人工审核后发送
        - "generic": 通用回复，不发送
        """

        should_auto, reason = cls.should_auto_reply(aggregated_result, context)

        if should_auto:
            return "auto"

        if aggregated_result.risk_level == RiskLevel.MEDIUM:
            return "human_review"

        return "human_review"


# ==================== Aggregator Agent ====================

class AggregatorAgent:
    """聚合器 Agent"""

    def __init__(self):
        self.quality_checker = ReplyQualityChecker()
        self.risk_assessor = RiskAssessor()
        self.reply_integrator = ReplyIntegrator()
        self.ai_optimizer = AIOptimizer()
        self.decision_engine = DecisionEngine()

    async def aggregate(
        self,
        context: InquiryContext,
        expert_responses: list[dict]
    ) -> AggregatedResult:
        """
        聚合多个专家回复，生成最终结果

        Args:
            context: 询盘上下文
            expert_responses: 各专家的回复列表

        Returns:
            AggregatedResult: 聚合结果
        """

        logger.info(f"聚合询盘 {context.inquiry_id}，专家回复数：{len(expert_responses)}")

        # 1. 转换为 ExpertResponse 对象
        responses = []
        for resp_data in expert_responses:
            responses.append(ExpertResponse(
                expert_name=resp_data.get("expert_name", ""),
                success=resp_data.get("success", False),
                reply=resp_data.get("reply", ""),
                confidence=resp_data.get("confidence", 0.0),
                requires_human=resp_data.get("requires_human", False),
                raw_data=resp_data.get("raw_data"),
                product_info=resp_data.get("product_info"),
                assessment=resp_data.get("assessment")
            ))

        # 2. 质量检查
        quality_result = self.quality_checker.check(
            "\n\n".join(r.reply for r in responses if r.success),
            responses,
            context
        )
        logger.info(f"质量检查：{quality_result}")

        # 3. 风险评估
        risk_level, risk_flags = self.risk_assessor.assess(
            context.inquiry_content,
            responses,
            context
        )
        logger.info(f"风险评估：{risk_level.value} - {risk_flags}")

        # 4. 整合回复
        final_reply = self.reply_integrator.integrate(
            responses,
            context,
            quality_result,
            risk_level
        )

        # 5. AI 优化（可选）
        optimized_reply = await self.ai_optimizer.optimize(
            final_reply,
            context,
            quality_result
        )
        if optimized_reply != final_reply:
            final_reply = optimized_reply
            logger.info("AI 优化回复完成")

        # 6. 计算综合置信度
        successful = [r for r in responses if r.success]
        if successful:
            avg_confidence = sum(r.confidence for r in successful) / len(successful)
            # 根据质量评分调整
            adjusted_confidence = avg_confidence * quality_result.get("weighted_score", 0.7)
        else:
            adjusted_confidence = 0.3

        # 7. 决策
        result = AggregatedResult(
            success=True,
            final_reply=final_reply,
            reply_type=self.decision_engine.determine_reply_type(
                AggregatedResult(
                    success=True,
                    final_reply=final_reply,
                    reply_type="",
                    confidence=adjusted_confidence,
                    risk_level=risk_level,
                    requires_human=any(r.requires_human for r in responses),
                    risk_flags=risk_flags,
                    expert_responses=responses,
                    suggestions=quality_result.get("suggestions", [])
                ),
                context
            ),
            confidence=adjusted_confidence,
            risk_level=risk_level,
            requires_human=any(r.requires_human for r in responses),
            risk_flags=risk_flags,
            expert_responses=responses,
            suggestions=quality_result.get("suggestions", [])
        )

        logger.info(f"聚合完成：reply_type={result.reply_type}, confidence={result.confidence:.2f}")

        return result


# ==================== FastAPI 接口 ====================

aggregator = AggregatorAgent()


class AggregateRequest(BaseModel):
    inquiry_id: str
    buyer_id: str
    buyer_name: str
    product_name: str
    product_category: str = ""
    inquiry_content: str
    inquiry_type: str
    timestamp: str
    expert_responses: list[dict]


@app.post("/aggregate")
async def aggregate_responses(request: AggregateRequest):
    """A2A 接口：聚合专家回复"""

    logger.info(f"聚合请求: {request.inquiry_id}")

    # 构建上下文
    context = InquiryContext(
        inquiry_id=request.inquiry_id,
        buyer_id=request.buyer_id,
        buyer_name=request.buyer_name,
        product_name=request.product_name,
        product_category=request.product_category,
        inquiry_content=request.inquiry_content,
        inquiry_type=InquiryType(request.inquiry_type),
        timestamp=request.timestamp
    )

    # 聚合
    result = await aggregator.aggregate(context, request.expert_responses)

    # 序列化
    return {
        "success": result.success,
        "final_reply": result.final_reply,
        "reply_type": result.reply_type,
        "confidence": result.confidence,
        "risk_level": result.risk_level.value,
        "requires_human": result.requires_human,
        "risk_flags": result.risk_flags,
        "suggestions": result.suggestions,
        "expert_summary": [
            {
                "expert_name": r.expert_name,
                "success": r.success,
                "confidence": r.confidence,
                "requires_human": r.requires_human
            }
            for r in result.expert_responses
        ]
    }


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "aggregator"}


@app.get("/decision")
async def decision_preview():
    """预览当前决策配置"""
    return {
        "auto_reply_threshold": DecisionEngine.AUTO_REPLY_THRESHOLD,
        "auto_reply_types": DecisionEngine.AUTO_REPLY_TYPES,
        "high_risk_keywords": RiskAssessor.HIGH_RISK_KEYWORDS,
        "medium_risk_keywords": RiskAssessor.MEDIUM_RISK_KEYWORDS
    }


@app.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card"""
    return {
        "name": "1688 Aggregator Agent",
        "description": "聚合器，汇总专家回复，质量检查，风险评估，生成最终回复",
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "supportedTasks": ["aggregate", "quality_check", "risk_assess"]
        },
        "endpoint": "http://aggregator-agent:8006",
        "skills": ["reply_integration", "quality_assessment", "risk_management"]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)

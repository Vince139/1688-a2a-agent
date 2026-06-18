"""
Customize Expert Agent - 定制专家
负责处理 OEM/ODM/定制类询盘，评估定制需求，引导至人工跟进
"""

import os
import re
import json
import logging
from typing import Optional, Any
from dataclasses import dataclass, field

import yaml
import aiohttp
from fastapi import FastAPI
from pydantic import BaseModel

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.a2a_client import A2AClientManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Customize Expert Agent")

# ==================== 配置 ====================
with open(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "config.yaml")) as f:
    config = yaml.safe_load(f)

with open(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "knowledge_base", "products.yaml")) as f:
    knowledge = yaml.safe_load(f)

A2A_ENDPOINTS = config["a2a"]["agents"]
REPLY_CONFIG = config.get("reply", {})
AI_CONFIG = config.get("ai", {})

a2a_client = A2AClientManager()
for name, url in A2A_ENDPOINTS.items():
    a2a_client.register(name, url)


# ==================== 数据模型 ====================

@dataclass
class CustomRequirement:
    """定制需求"""
    requirement_type: str  # "logo" | "packaging" | "color" | "spec" | "full_odm" | "other"
    description: str
    has_design: bool  # 是否有设计稿
    has_brand: bool   # 是否有品牌
    target_quantity: Optional[int]
    target_price: Optional[str]
    timeline: Optional[str]
    priority: str  # "high" | "medium" | "low"


@dataclass
class CustomCapability:
    """定制能力"""
    product_name: str
    moq: str
    moq_custom: str  # 定制起订量
    lead_time: str
    lead_time_custom: str  # 定制交期
    customization_types: list[str]
    design_support: bool
    packaging_options: list[str]
    certifications: list[str]
    moq_notes: str
    price_indicative: str  # 参考价格区间


@dataclass
class CustomAssessment:
    """定制需求评估"""
    requirements: list[CustomRequirement]
    matched_products: list[CustomCapability]
    feasibility: str  # "high" | "medium" | "low" | "uncertain"
    feasibility_reasons: list[str]
    needs_discussion: list[str]  # 需要进一步讨论的点
    is_complex: bool  # 是否复杂需要人工跟进
    estimated_moq: str
    estimated_lead_time: str
    estimated_price_range: str


# ==================== 知识库 ====================

class CustomKnowledgeBase:
    """定制能力知识库"""

    def __init__(self, knowledge_data: dict):
        self.data = knowledge_data

    def find_capability(self, query: str) -> Optional[CustomCapability]:
        """查找商品的定制能力"""
        query_lower = query.lower()

        for category, items in self.data.items():
            for item in items:
                name = item.get("name", "")
                keywords = item.get("keywords", [])

                if (query_lower in name.lower() or
                    any(query_lower in kw.lower() for kw in keywords)):
                    return self._build_capability(item)

        return None

    def _build_capability(self, item: dict) -> CustomCapability:
        """从知识库条目构建定制能力"""
        customization = item.get("customization", "")

        # 解析支持的定制类型
        types = []
        if "logo" in customization.lower() or "定制logo" in customization:
            types.append("logo")
        if "包装" in customization:
            types.append("packaging")
        if "颜色" in customization:
            types.append("color")
        if "规格" in customization:
            types.append("spec")
        if "品牌" in customization or "贴牌" in customization:
            types.append("brand")

        # 解析 MOQ
        moq_match = re.search(r"MOQ\s*(\d+)", customization, re.IGNORECASE)
        moq_custom = moq_match.group(1) if moq_match else "500"

        # 解析交期
        delivery = item.get("delivery_time", "15-25天")
        if "定制" in customization:
            match = re.search(r"(\d+)-(\d+)天", delivery)
            if match:
                base_min, base_max = int(match.group(1)), int(match.group(2))
                lead_time_custom = f"{base_min + 5}-{base_max + 10}天"
            else:
                lead_time_custom = "20-35天"
        else:
            lead_time_custom = delivery

        return CustomCapability(
            product_name=item.get("name", ""),
            moq=item.get("moq", ""),
            moq_custom=moq_custom,
            lead_time=delivery,
            lead_time_custom=lead_time_custom,
            customization_types=types,
            design_support=True,  # 大多数工厂支持
            packaging_options=self._parse_packaging(customization),
            certifications=item.get("certifications", []),
            moq_notes=f"标准品MOQ {item.get('moq', '')}，定制MOQ {moq_custom}件起",
            price_indicative=item.get("price_range", "")
        )

    def _parse_packaging(self, customization: str) -> list[str]:
        options = []
        if "包装" in customization:
            options.append("公版包装")
        if "logo" in customization.lower():
            options.append("定制包装盒")
        if "品牌" in customization or "贴牌" in customization:
            options.append("品牌包装整套")
        if not options:
            options.append("常规包装")
        return options

    def get_all_capabilities(self) -> list[CustomCapability]:
        caps = []
        for category, items in self.data.items():
            for item in items:
                caps.append(self._build_capability(item))
        return caps


# ==================== 需求解析 ====================

class CustomRequirementParser:
    """定制需求解析"""

    # 定制类型关键词
    TYPE_PATTERNS = {
        "logo": ["logo", "贴牌", "定制logo", "印logo", "品牌定制", "品牌贴牌"],
        "packaging": ["包装", "盒", "瓶身", "公版包装", "彩盒"],
        "color": ["颜色", "定制颜色", "指定颜色", "配色"],
        "spec": ["规格", "尺寸", "容量", "材质", "特殊规格", "参数"],
        "full_odm": ["odm", "oem", "全案", "从0开始", "自主设计", "研发"],
        "formula": ["配方", "成分", "原料", "定制配方"],
    }

    # 意图强度关键词
    INTENT_STRONG = ["要", "想做", "需要", "想定制", "计划", "目标"]
    INTENT_WEAK = ["了解", "看看", "咨询", "问一下", "有没有", "可以吗"]

    @classmethod
    def parse(cls, text: str) -> list[CustomRequirement]:
        """解析文本中的定制需求"""
        requirements = []
        text_lower = text.lower()

        for req_type, patterns in cls.TYPE_PATTERNS.items():
            for pattern in patterns:
                if pattern.lower() in text_lower:
                    req = cls._build_requirement(text, req_type, pattern)
                    if req and req not in requirements:
                        requirements.append(req)
                    break

        # 提取数量
        qty_match = re.search(r"(\d+)\s*(?:件|套|个|瓶|支)", text)
        target_qty = int(qty_match.group(1)) if qty_match else None

        # 提取目标价格
        price_match = re.search(r"[\¥\￥]?\s*(\d+(?:\.\d+)?)\s*(?:元|块)", text)
        target_price = f"¥{price_match.group(1)}" if price_match else None

        # 提取时间线
        timeline = None
        timeline_patterns = [
            r"(\d+\s*(?:周|个月|月|天))",
            r"(尽快|越快越好|不着急)",
            r"(下个月|本月|这季度)"
        ]
        for pattern in timeline_patterns:
            match = re.search(pattern, text)
            if match:
                timeline = match.group(1)
                break

        # 判断是否已有设计/品牌
        has_design = any(kw in text for kw in ["设计稿", "效果图", "图", "稿", "稿子", "有设计"])
        has_brand = any(kw in text for kw in ["品牌", "牌子", "商标", "品牌名"])

        # 意图强度
        intent = "medium"
        if any(kw in text for kw in cls.INTENT_STRONG):
            intent = "high"
        elif any(kw in text for kw in cls.INTENT_WEAK):
            intent = "low"

        # 为没有类型的添加通用类型
        if not requirements:
            requirements.append(CustomRequirement(
                requirement_type="other",
                description=text[:200],
                has_design=has_design,
                has_brand=has_brand,
                target_quantity=target_qty,
                target_price=target_price,
                timeline=timeline,
                priority=intent
            ))

        # 更新各需求的通用信息
        for req in requirements:
            if req.target_quantity is None:
                req.target_quantity = target_qty
            if req.target_price is None:
                req.target_price = target_price
            if req.timeline is None:
                req.timeline = timeline
            req.has_design = req.has_design or has_design
            req.has_brand = req.has_brand or has_brand

        return requirements

    @classmethod
    def _build_requirement(cls, text: str, req_type: str, matched_pattern: str) -> Optional[CustomRequirement]:
        """构建需求对象"""
        # 提取该需求相关的描述
        desc_match = re.search(
            rf"[。；，,.！!？?\n]?[^。；，,！!？?\n]{{0,50}}{matched_pattern}[^。；，,！!？?\n]{{0,50}}",
            text
        )
        description = desc_match.group(0) if desc_match else text[:100]

        return CustomRequirement(
            requirement_type=req_type,
            description=description.strip(),
            has_design=False,
            has_brand=False,
            target_quantity=None,
            target_price=None,
            timeline=None,
            priority="medium"
        )

    @staticmethod
    def assess_complexity(requirements: list[CustomRequirement], text: str) -> bool:
        """评估需求是否复杂，需要人工跟进"""
        complexity_flags = 0

        # 多个定制类型
        if len(set(r.requirement_type for r in requirements)) > 2:
            complexity_flags += 1

        # 涉及 ODM/OEM 全案
        if any(r.requirement_type == "full_odm" for r in requirements):
            complexity_flags += 2

        # 没有明确数量
        if not any(r.target_quantity for r in requirements):
            complexity_flags += 1

        # 涉及价格谈判
        if "价格" in text or "便宜" in text or "贵" in text:
            complexity_flags += 1

        # 客户主动提供设计/品牌（表示认真度较高）
        if any(r.has_design or r.has_brand for r in requirements):
            complexity_flags += 1

        # 账期/付款方式提及
        risk_keywords = REPLY_CONFIG.get("high_risk_keywords", [])
        for kw in risk_keywords:
            if kw in text:
                complexity_flags += 1

        return complexity_flags >= 3


# ==================== 评估逻辑 ====================

class CustomAssessor:
    """定制需求评估器"""

    def __init__(self, kb: CustomKnowledgeBase):
        self.kb = kb

    def assess(self, text: str, product_name: str) -> CustomAssessment:
        """评估定制需求"""

        # 解析需求
        requirements = CustomRequirementParser.parse(text)

        # 查找匹配产品
        matched = self.kb.find_capability(product_name)
        matched_products = [matched] if matched else []

        if not matched_products:
            # 尝试全文搜索
            for kw in requirements[0].description.split()[:3]:
                cap = self.kb.find_capability(kw)
                if cap and cap not in matched_products:
                    matched_products.append(cap)

        # 评估可行性
        feasibility, reasons = self._evaluate_feasibility(requirements, matched_products)

        # 识别需要讨论的点
        needs_discussion = self._identify_discussion_points(requirements, matched_products)

        # 复杂度判断
        is_complex = CustomRequirementParser.assess_complexity(requirements, text)

        # 估算参数
        est_moq = matched_products[0].moq_custom if matched_products else "待确认"
        est_lead = matched_products[0].lead_time_custom if matched_products else "待确认"
        est_price = matched_products[0].price_indicative if matched_products else "待确认"

        return CustomAssessment(
            requirements=requirements,
            matched_products=matched_products,
            feasibility=feasibility,
            feasibility_reasons=reasons,
            needs_discussion=needs_discussion,
            is_complex=is_complex,
            estimated_moq=est_moq,
            estimated_lead_time=est_lead,
            estimated_price_range=est_price
        )

    def _evaluate_feasibility(
        self,
        requirements: list[CustomRequirement],
        products: list[CustomCapability]
    ) -> tuple[str, list[str]]:
        """评估可行性"""
        reasons = []
        feasibility = "high"

        if not products:
            return "uncertain", ["商品信息未匹配，请联系客服确认"]

        for req in requirements:
            if req.requirement_type == "full_odm":
                if products[0].customization_types:
                    reasons.append("✅ 支持 OEM/ODM 全案定制")
                else:
                    reasons.append("⚠️ 全案定制需进一步确认设计能力")
                    feasibility = "medium"

            elif req.requirement_type == "logo":
                if "logo" in products[0].customization_types:
                    reasons.append(f"✅ 支持定制logo，MOQ {products[0].moq_custom}")
                else:
                    reasons.append("❌ 该商品不支持logo定制")

            elif req.requirement_type == "packaging":
                if products[0].packaging_options:
                    reasons.append(f"✅ 支持包装定制：{'/'.join(products[0].packaging_options)}")
                else:
                    feasibility = "medium"

            elif req.requirement_type == "color":
                if "color" in products[0].customization_types:
                    reasons.append("✅ 支持定制颜色")
                else:
                    reasons.append("⚠️ 颜色定制需确认色板和起订量")

        if not reasons:
            reasons.append("✅ 定制需求可以满足，具体需沟通细节")
            feasibility = "medium"

        return feasibility, reasons

    def _identify_discussion_points(
        self,
        requirements: list[CustomRequirement],
        products: list[CustomCapability]
    ) -> list[str]:
        """识别需要讨论的点"""
        points = []

        # 数量
        if not any(r.target_quantity for r in requirements):
            points.append("🎯 目标订购数量是多少？")

        # 设计稿
        if not any(r.has_design for r in requirements):
            points.append("📐 是否有设计稿/效果图？如没有，我们可以提供设计支持")

        # 品牌
        if not any(r.has_brand for r in requirements):
            points.append("🏷️ 是否有品牌/商标？需要提供授权文件")

        # 时间线
        if not any(r.timeline for r in requirements):
            points.append("⏰ 期望的交货时间是什么时候？")

        # 价格预期
        if not any(r.target_price for r in requirements):
            points.append("💰 目标采购单价或总预算大概是多少？")

        return points


# ==================== 回复生成 ====================

class CustomReplyGenerator:
    """定制回复生成器"""

    def generate(self, assessment: CustomAssessment, buyer_name: str) -> str:
        """生成定制询盘回复"""

        parts = []

        # 1. 问候
        parts.append(f"尊敬的 {buyer_name}，您好！感谢您咨询定制业务~ 🏭")
        parts.append("")
        parts.append("我们专业支持 OEM/ODM 定制服务，以下是您的定制需求评估：")
        parts.append("")

        # 2. 需求摘要
        if assessment.matched_products:
            product = assessment.matched_products[0]
            parts.append(f"**📦 匹配产品：** {product.product_name}")
            parts.append("")

        parts.append("**🔍 您的定制需求：**")
        for req in assessment.requirements:
            type_map = {
                "logo": "Logo/品牌定制",
                "packaging": "包装定制",
                "color": "颜色定制",
                "spec": "规格定制",
                "full_odm": "OEM/ODM 全案",
                "formula": "配方定制",
                "other": "其他定制"
            }
            parts.append(f"- **{type_map.get(req.requirement_type, req.requirement_type)}**：{req.description[:50]}")
        parts.append("")

        # 3. 可行性评估
        parts.append("**✅ 可行性评估：**")
        for reason in assessment.feasibility_reasons:
            parts.append(reason)
        parts.append("")
        parts.append(f"📊 整体评估：{assessment.feasibility.upper()}")
        parts.append("")

        # 4. 初步参数估算
        parts.append("**📋 初步估算参数：**")
        parts.append(f"- 起订量（MOQ）：{assessment.estimated_moq}")
        parts.append(f"- 交期：{assessment.estimated_lead_time}")
        parts.append(f"- 参考价格：{assessment.estimated_price_range}")
        parts.append("")

        # 5. 需要讨论的点
        if assessment.needs_discussion:
            parts.append("**💬 为您提供更准确的方案，需要确认：**")
            for point in assessment.needs_discussion:
                parts.append(point)
            parts.append("")

        # 6. 我们的优势
        if assessment.matched_products:
            product = assessment.matched_products[0]
            parts.append("**🏭 我们的定制能力：**")
            if product.certifications:
                parts.append(f"- 认证齐全：{'/'.join(product.certifications[:3])}")
            if product.design_support:
                parts.append("- 提供专业设计支持")
            if product.packaging_options:
                parts.append(f"- 包装方案：{'/'.join(product.packaging_options)}")
            parts.append("")

        # 7. 行动引导
        parts.append("---")
        parts.append("**📞 下一步：**")
        parts.append("")
        parts.append("定制业务通常需要详细沟通，为确保为您提供准确方案，建议：")
        parts.append("")

        if assessment.is_complex:
            # 复杂需求，建议电话/视频
            parts.append("1️⃣ **加微信深入沟通**（推荐）— 可发送设计稿、确认细节")
            parts.append("2️⃣ **安排电话/视频会议** — 快速明确需求")
            parts.append("3️⃣ **来厂参观考察** — 实地了解我们的生产能力")
            parts.append("")
            parts.append("我们的销售经理将为您提供一对一服务，预计响应时间 **30分钟内** ☎️")
        else:
            # 简单需求，在线沟通
            parts.append("1️⃣ 请回复以上需要确认的信息")
            parts.append("2️⃣ 我们为您出具详细定制方案和报价单")
            parts.append("3️⃣ 确认后安排打样，样品确认后大货生产")
            parts.append("")
            parts.append("期待与您合作！🎉")

        return "\n".join(parts)

    def generate_generic_reply(self, buyer_name: str, text: str) -> str:
        """通用回复（未匹配商品时）"""
        return (
            f"尊敬的 {buyer_name}，您好！感谢您咨询定制业务~ 🏭\n\n"
            f"我们是一家专业从事OEM/ODM的工厂，支持以下定制服务：\n\n"
            f"**🎨 定制类型：**\n"
            f"- Logo/品牌贴牌定制\n"
            f"- 包装定制（彩盒、瓶身、标签等）\n"
            f"- 颜色/规格定制\n"
            f"- 全案OEM/ODM（从设计到出货）\n\n"
            f"**📦 品类覆盖：**\n"
            f"- 户外运动类：背包、帐篷、运动服饰、水壶等\n"
            f"- 美妆护肤类：护肤品、彩妆、香水、化妆工具等\n\n"
            f"**🏭 我们的优势：**\n"
            f"- 源头工厂，价格有竞争力\n"
            f"- 10年+出口经验，认证齐全\n"
            f"- 支持小批量定制，MOQ灵活\n"
            f"- 专业设计团队，提供设计方案\n\n"
            f"请告诉我您想定制什么产品、有什么具体需求，"
            f"我们会尽快为您提供方案和报价！"
        )


# ==================== Custom Expert Agent ====================

class CustomExpertAgent:
    """定制专家 Agent"""

    def __init__(self):
        self.kb = CustomKnowledgeBase(knowledge)
        self.assessor = CustomAssessor(self.kb)
        self.reply_gen = CustomReplyGenerator()
        self.ai_config = AI_CONFIG

    async def call_ai(self, prompt: str) -> Optional[str]:
        """调用 AI 优化回复"""
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
            logger.warning(f"AI 调用失败: {e}")

        return None

    async def process(
        self,
        inquiry_content: str,
        buyer_name: str,
        product_name: str,
        context: dict = None
    ) -> dict:
        """处理定制询盘"""

        result = {
            "success": False,
            "assessment": None,
            "reply": "",
            "confidence": 0.0,
            "requires_human": True  # 定制类默认人工跟进
        }

        # 1. 评估需求
        assessment = self.assessor.assess(inquiry_content, product_name)
        result["assessment"] = assessment

        # 2. 生成回复
        if assessment.matched_products:
            reply = self.reply_gen.generate(assessment, buyer_name)
        else:
            reply = self.reply_gen.generate_generic_reply(buyer_name, inquiry_content)

        # 3. AI 优化
        ai_reply = await self.call_ai(
            f"你是一个1688 OEM/ODM定制业务经理。买家[{buyer_name}]咨询定制：\n"
            f"产品：{product_name}\n"
            f"需求：{inquiry_content}\n"
            f"可行性：{assessment.feasibility}\n"
            f"需确认：{assessment.needs_discussion}\n\n"
            f"请生成一条专业回复，引导客户进一步沟通，200字以内。"
        )

        result["reply"] = ai_reply if ai_reply else reply

        # 4. 置信度
        if assessment.matched_products and assessment.feasibility == "high":
            result["confidence"] = 0.85
        elif assessment.matched_products:
            result["confidence"] = 0.7
        else:
            result["confidence"] = 0.5

        # 5. 定制类默认需要人工（除非非常简单且需求明确）
        if not assessment.is_complex and assessment.matched_products:
            if assessment.feasibility == "high" and len(assessment.needs_discussion) <= 2:
                result["requires_human"] = False

        result["success"] = True
        return result


# ==================== FastAPI 接口 ====================

custom_expert = CustomExpertAgent()


class InquiryRequest(BaseModel):
    inquiry_content: str
    buyer_name: str
    product_name: str
    context: dict = None


@app.post("/process")
async def process_inquiry(request: InquiryRequest):
    """A2A 接口：处理定制询盘"""
    logger.info(f"处理定制询盘: {request.inquiry_content[:80]}")

    result = await custom_expert.process(
        inquiry_content=request.inquiry_content,
        buyer_name=request.buyer_name,
        product_name=request.product_name,
        context=request.context
    )

    assessment = result["assessment"]
    return {
        "success": result["success"],
        "confidence": result["confidence"],
        "requires_human": result["requires_human"],
        "reply": result["reply"],
        "assessment": {
            "feasibility": assessment.feasibility if assessment else "uncertain",
            "feasibility_reasons": assessment.feasibility_reasons if assessment else [],
            "needs_discussion": assessment.needs_discussion if assessment else [],
            "estimated_moq": assessment.estimated_moq if assessment else "",
            "estimated_lead_time": assessment.estimated_lead_time if assessment else "",
            "requirements": [
                {"type": r.requirement_type, "description": r.description}
                for r in (assessment.requirements if assessment else [])
            ]
        }
    }


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "customize_expert"}


@app.get("/capabilities")
async def list_capabilities():
    """列出所有商品的定制能力"""
    caps = custom_expert.kb.get_all_capabilities()
    return {
        "count": len(caps),
        "capabilities": [
            {
                "product_name": c.product_name,
                "moq_custom": c.moq_custom,
                "lead_time_custom": c.lead_time_custom,
                "customization_types": c.customization_types,
                "packaging_options": c.packaging_options
            }
            for c in caps
        ]
    }


@app.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card"""
    return {
        "name": "1688 Customize Expert Agent",
        "description": "定制专家，处理OEM/ODM/品牌定制询盘，评估可行性，引导人工跟进",
        "version": "1.0.0",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "supportedTasks": ["customization_consult", "oem_inquiry", "odm_inquiry"]
        },
        "endpoint": "http://customize-expert:8005",
        "skills": ["custom_capability", "feasibility_assessment", "oem_odm"]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)

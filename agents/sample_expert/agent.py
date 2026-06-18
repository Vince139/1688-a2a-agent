"""
Sample Expert Agent - 样品专家
负责处理样品申请类询盘，介绍样品政策、流程、费用等
"""

import os
import re
import json
import logging
from typing import Optional, Any
from dataclasses import dataclass

import yaml
import aiohttp
from fastapi import FastAPI
from pydantic import BaseModel

import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.a2a_client import A2AClientManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Sample Expert Agent")

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
class SamplePolicy:
    """样品政策"""
    product_name: str
    sample_fee: str
    sample_fee_refundable: bool
    shipping_fee: str
    sample_time: str
    moq_after_sample: str
    customization_available: bool
    notes: list


@dataclass
class SampleApplication:
    """样品申请信息"""
    products: list[str]
    quantities: list[int]
    application_type: str  # "single" | "multiple" | "trial"
    buyer_intent: str  # "retail" | "wholesale" | "customization"


# ==================== 知识库 ====================

class SampleKnowledgeBase:
    """样品知识库"""

    def __init__(self, knowledge_data: dict):
        self.data = knowledge_data
        self._build_index()

    def _build_index(self):
        self.product_index = {}
        self._recursive_index(self.data)

    def _recursive_index(self, data):
        if isinstance(data, dict):
            for v in data.values():
                self._recursive_index(v)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    for kw in item.get("keywords", []):
                        self.product_index[kw.lower()] = item
                    for v in item.values():
                        self._recursive_index(v)

    def find_product(self, query: str) -> Optional[dict]:
        query_lower = query.lower()
        for kw, item in self.product_index.items():
            if kw in query_lower or query_lower in kw:
                return item
        return self._find_by_name(query_lower, self.data)

    def _find_by_name(self, query: str, data) -> Optional[dict]:
        if isinstance(data, dict):
            for v in data.values():
                result = self._find_by_name(query, v)
                if result:
                    return result
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    name = item.get("name", "")
                    if name and (query in name.lower() or name.lower() in query):
                        return item
        return None

    def get_sample_policy(self, product_item: dict) -> SamplePolicy:
        sample_fee = product_item.get("sample_fee", "")
        is_refundable = "可退" in sample_fee

        return SamplePolicy(
            product_name=product_item.get("name", ""),
            sample_fee=sample_fee,
            sample_fee_refundable=is_refundable,
            shipping_fee="买家承担（到付/预付）",
            sample_time=self._estimate_sample_time(product_item),
            moq_after_sample=product_item.get("moq", ""),
            customization_available="定制" in product_item.get("customization", ""),
            notes=self._generate_notes(product_item)
        )

    def _estimate_sample_time(self, item: dict) -> str:
        delivery = item.get("delivery_time", "10-15天")
        parts = delivery.split("-")
        if len(parts) == 2:
            try:
                min_days = int(parts[0])
                max_days = int(parts[1].replace("天", ""))
                sample_days = max(3, min_days // 2)
                return f"{sample_days}-{max_days}天"
            except:
                pass
        return delivery

    def _generate_notes(self, item: dict) -> list:
        notes = []
        if "可退" in item.get("sample_fee", ""):
            notes.append("样品费下单后可退")
        if "定制" in item.get("customization", ""):
            notes.append("支持定制样品")
        certs = item.get("certifications", [])
        if certs:
            notes.append(f"已通过认证：{'/'.join(certs[:2])}")
        return notes


# ==================== 样品流程解析 ====================

class SampleParser:
    """样品询盘解析"""

    @staticmethod
    def parse_application(text: str) -> SampleApplication:
        """解析样品申请内容"""
        products = []
        quantities = []

        # 提取商品名称
        product_patterns = [
            r"([\u4e00-\u9fa5]+(?:帐篷|背包|水壶|面膜|口红|护肤套装|瑜伽垫))",
            r"([\u4e00-\u9fa5]+(?:款|型号|产品))",
        ]
        for pattern in product_patterns:
            matches = re.findall(pattern, text)
            products.extend(matches)

        # 提取数量
        qty_patterns = [
            r"(\d+)\s*[Xx×]\s*\d+",  # 3款x2件
            r"(\d+)\s*(?:款|个|件)",  # 3款
            r"各?\s*(\d+)\s*(?:件|个)",  # 各2件
        ]
        for pattern in qty_patterns:
            matches = re.findall(pattern, text)
            quantities = [int(m) for m in matches]
            if quantities:
                break

        # 判断申请类型
        if "多" in text or len(products) > 1 or "几款" in text or "哪些" in text:
            app_type = "multiple"
        elif "试用" in text or "试" in text:
            app_type = "trial"
        else:
            app_type = "single"

        # 判断购买意图
        if "定制" in text or "贴牌" in text or "OEM" in text.upper():
            intent = "customization"
        elif "批发" in text or "拿货" in text or "进货" in text:
            intent = "wholesale"
        else:
            intent = "retail"

        return SampleApplication(
            products=products if products else [],
            quantities=quantities if quantities else [1],
            application_type=app_type,
            buyer_intent=intent
        )

    @staticmethod
    def extract_worry_points(text: str) -> list[str]:
        """识别买家关心的问题"""
        worries = []

        worry_keywords = {
            "price": ["贵", "便宜", "价格", "费用", "钱"],
            "quality": ["质量", "品质", "怕", "担心", "正品"],
            "time": ["多久", "时间", "几天", "快点"],
            "refund": ["退", "退钱", "退款", "可退"],
            "shipping": ["运费", "邮费", "到付"],
            "moq": ["起订", "最小", "多少起"],
            "custom": ["定制", "logo", "贴牌", "设计"],
        }

        text_lower = text
        for category, keywords in worry_keywords.items():
            if any(kw in text_lower for kw in keywords):
                worries.append(category)

        return worries


# ==================== 回复生成 ====================

class SampleReplyGenerator:
    """样品回复生成器"""

    def __init__(self):
        self.template_steps = [
            ("step1", "📋 **样品申请流程**"),
            ("step2", "💰 **费用说明**"),
            ("step3", "📦 **样品时间**"),
            ("step4", "🤝 **后续合作**"),
        ]

    def generate(
        self,
        buyer_name: str,
        products: list[str],
        policies: list[SamplePolicy],
        application: SampleApplication,
        worries: list[str]
    ) -> str:
        """生成完整的样品回复"""

        parts = []

        # 1. 问候 + 表达欢迎
        parts.append(f"尊敬的 {buyer_name}，您好！感谢您对我们的产品感兴趣~ 🌟")
        parts.append("")

        # 2. 处理多款商品
        if len(policies) == 1:
            parts.append(f"针对您咨询的 **{policies[0].product_name}** 样品，")
        elif len(policies) > 1:
            product_names = "、".join([p.product_name for p in policies[:3]])
            if len(policies) > 3:
                product_names += f"等{len(policies)}款产品"
            parts.append(f"针对您咨询的 **{product_names}** 样品，")
        else:
            parts.append("针对您咨询的样品，")

        parts.append("")
        parts.append("为您详细介绍样品政策：")
        parts.append("")

        # 3. 样品费用表格
        parts.append("**💰 样品费用明细**")
        parts.append("")
        parts.append("| 商品 | 样品费 | 运费 | 备注 |")
        parts.append("|------|--------|------|------|")

        for policy in policies:
            refund_note = "可退" if policy.sample_fee_refundable else "不退"
            parts.append(f"| {policy.product_name} | {policy.sample_fee} | {policy.shipping_fee} | {refund_note} |")

        parts.append("")

        # 4. 流程说明
        parts.append("**📋 样品申请流程**")
        parts.append("")
        parts.append("① 确认您需要的样品型号/数量")
        parts.append("② 我们为您出具样品费报价")
        parts.append("③ 您付款后我们安排发货")
        parts.append("④ 收到样品确认品质")
        parts.append("⑤ 正式下单后，样品费可抵扣货款 ✅")
        parts.append("")

        # 5. 针对买家关心的问题重点说明
        if "price" in worries:
            parts.append("**💡 关于费用：**")
            for policy in policies[:2]:
                parts.append(f"- {policy.product_name}：{policy.sample_fee}")
            if policies and policies[0].sample_fee_refundable:
                parts.append("- 样品费正式下单后 **可全额抵扣** 🏷️")
            parts.append("")

        if "quality" in worries:
            parts.append("**🔍 关于品质：**")
            parts.append("- 我们是源头工厂，产品品质严格把控")
            parts.append("- 可提供产品检测报告/认证证书")
            parts.append("- 支持第三方验货")
            parts.append("")

        if "time" in worries:
            parts.append("**⏰ 关于时间：**")
            for policy in policies[:2]:
                parts.append(f"- {policy.product_name}：付款后 **{policy.sample_time}** 发货")
            parts.append("")

        if "refund" in worries:
            if policies and any(p.sample_fee_refundable for p in policies):
                parts.append("**💵 关于退款：**")
                parts.append("- 样品费在您正式下单后可全额抵扣货款")
                parts.append("- 如未下单，样品按采购成本价核算，协商退款")
                parts.append("")

        if "shipping" in worries:
            parts.append("**📦 关于运费：**")
            parts.append("- 样品小件默认发快递（顺丰/中通）")
            parts.append("- 到付或预付均可，我们建议到付更方便")
            parts.append("")

        if "custom" in worries or "moq" in worries:
            parts.append("**🎨 关于定制/MOQ：**")
            for policy in policies:
                if policy.customization_available:
                    parts.append(f"- {policy.product_name}：支持定制logo/颜色，MOQ {policy.moq_after_sample}")
                parts.append(f"- {policy.product_name}：正式订单 {policy.moq_after_sample}")
            parts.append("")

        # 6. 多款样品特殊处理
        if application.application_type == "multiple" and len(products) > 1:
            parts.append("---")
            parts.append("📝 **温馨提示：**")
            parts.append("多款样品我们可以一并安排，欢迎您列出具体型号！")
            parts.append("")

        # 7. 行动号召
        parts.append("---")
        parts.append("**💬 下一步操作：**")
        parts.append("1️⃣ 请告诉我您需要哪些型号/多少件样品？")
        parts.append("2️⃣ 我们为您出具详细报价单")
        parts.append("3️⃣ 您确认后付款，我们48小时内发货")
        parts.append("")
        parts.append("期待与您合作，祝您生意兴隆！🎉")

        return "\n".join(parts)

    def generate_generic_reply(self, buyer_name: str) -> str:
        """通用样品回复（未匹配到商品时）"""
        return (
            f"尊敬的 {buyer_name}，您好！感谢您对我们的产品感兴趣~ 🌟\n\n"
            f"我们支持样品服务，具体政策如下：\n\n"
            f"**💰 样品费用：**\n"
            f"- 样品费根据产品而定，大部分可退\n"
            f"- 运费由买家承担（到付/预付均可）\n\n"
            f"**📋 流程：**\n"
            f"1. 告诉我您想要的产品型号\n"
            f"2. 我们出具样品费报价\n"
            f"3. 付款后48小时内发货\n"
            f"4. 正式下单后样品费可抵扣货款\n\n"
            f"**📦 合作后优惠：**\n"
            f"- 样品费全额抵扣货款\n"
            f"- 批量订单享阶梯价\n"
            f"- 支持OEM/ODM定制\n\n"
            f"请问您想了解哪些产品的样品呢？"
        )


# ==================== Sample Expert Agent ====================

class SampleExpertAgent:
    """样品专家 Agent"""

    def __init__(self):
        self.kb = SampleKnowledgeBase(knowledge)
        self.reply_gen = SampleReplyGenerator()
        self.ai_config = AI_CONFIG

    async def call_ai(self, prompt: str) -> Optional[str]:
        """调用 AI 优化回复"""
        provider = self.ai_config.get("provider", "hermes")
        if provider != "hermes":
            return None

        hermes_config = self.ai_config.get("hermes", {})
        api_url = hermes_config.get("api_url", "http://hermes:8000")
        api_key = hermes_config.get("api_key", "")

        payload = {"message": prompt, "api_key": api_key}

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{api_url}/chat",
                    json=payload,
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
        """
        处理样品询盘

        返回:
            {
                "success": bool,
                "policies": list[SamplePolicy],
                "reply": str,
                "confidence": float,
                "requires_human": bool
            }
        """

        result = {
            "success": False,
            "policies": [],
            "reply": "",
            "confidence": 0.0,
            "requires_human": False
        }

        # 1. 解析申请内容
        application = SampleParser.parse_application(inquiry_content)
        worries = SampleParser.extract_worry_points(inquiry_content)

        # 2. 匹配商品
        products_to_match = application.products if application.products else [product_name, inquiry_content]
        matched_items = []
        policies = []

        for prod in products_to_match:
            item = self.kb.find_product(prod)
            if item and item not in matched_items:
                matched_items.append(item)
                policies.append(self.kb.get_sample_policy(item))

        result["policies"] = policies

        if not policies:
            # 未匹配到商品
            result["reply"] = self.reply_gen.generate_generic_reply(buyer_name)
            result["confidence"] = 0.4
            result["requires_human"] = True
            return result

        # 3. 生成回复
        reply = self.reply_gen.generate(
            buyer_name=buyer_name,
            products=application.products,
            policies=policies,
            application=application,
            worries=worries
        )

        # 4. AI 优化
        ai_reply = await self.call_ai(
            f"你是一个1688外贸业务员。买家[{buyer_name}]申请样品：\n"
            f"产品：{application.products}\n"
            f"数量：{application.quantities}\n"
            f"意图：{application.buyer_intent}\n"
            f"关心的点：{worries}\n\n"
            f"请用专业热情的语气生成样品申请回复，200字以内，包含费用、流程、优惠信息。"
        )

        result["reply"] = ai_reply if ai_reply else reply

        # 5. 置信度
        result["confidence"] = self._calc_confidence(application, policies)

        # 6. 高风险判断
        high_risk = REPLY_CONFIG.get("high_risk_keywords", [])
        if any(kw in inquiry_content for kw in high_risk):
            result["requires_human"] = True

        result["success"] = True
        return result

    def _calc_confidence(self, app: SampleApplication, policies: list) -> float:
        """计算置信度"""
        confidence = 0.6

        if policies:
            confidence += 0.15

        if app.products:
            confidence += 0.1

        if app.application_type == "single":
            confidence += 0.1

        if app.buyer_intent in ["wholesale", "customization"]:
            confidence += 0.05

        return min(confidence, 0.95)


# ==================== FastAPI 接口 ====================

sample_expert = SampleExpertAgent()


class InquiryRequest(BaseModel):
    inquiry_content: str
    buyer_name: str
    product_name: str
    context: dict = None


@app.post("/process")
async def process_inquiry(request: InquiryRequest):
    """A2A 接口：处理样品询盘"""
    logger.info(f"处理样品询盘: {request.inquiry_content[:80]}")

    result = await sample_expert.process(
        inquiry_content=request.inquiry_content,
        buyer_name=request.buyer_name,
        product_name=request.product_name,
        context=request.context
    )

    return {
        "success": result["success"],
        "confidence": result["confidence"],
        "requires_human": result["requires_human"],
        "reply": result["reply"],
        "policies": [
            {
                "product_name": p.product_name,
                "sample_fee": p.sample_fee,
                "sample_fee_refundable": p.sample_fee_refundable,
                "shipping_fee": p.shipping_fee,
                "sample_time": p.sample_time,
                "moq_after_sample": p.moq_after_sample,
                "notes": p.notes
            }
            for p in result["policies"]
        ]
    }


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "sample_expert"}


@app.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card"""
    return {
        "name": "1688 Sample Expert Agent",
        "description": "样品专家，处理样品申请、费用说明、样品流程咨询",
        "version": "1.0.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "supportedTasks": ["sample_application", "sample_consult"]
        },
        "endpoint": "http://sample-expert:8004",
        "skills": ["sample_policy", "sample_process", "fee_calculation"]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)

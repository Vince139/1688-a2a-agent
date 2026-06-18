"""
Price Expert Agent - 价格专家
负责处理价格咨询类询盘，查询知识库生成专业报价回复
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

# 导入 A2A 相关
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from shared.a2a_client import A2AClientManager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="Price Expert Agent")

# ==================== 配置 ====================
with open(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "config.yaml")) as f:
    config = yaml.safe_load(f)

with open(os.path.join(os.path.dirname(__file__), "..", "..", "shared", "knowledge_base", "products.yaml")) as f:
    knowledge = yaml.safe_load(f)

A2A_ENDPOINTS = config["a2a"]["agents"]
REPLY_CONFIG = config.get("reply", {})
AI_CONFIG = config.get("ai", {})

# A2A 客户端
a2a_client = A2AClientManager()
for name, url in A2A_ENDPOINTS.items():
    a2a_client.register(name, url)

# ==================== 数据模型 ====================

@dataclass
class Product:
    """商品信息"""
    name: str
    price_range: str
    moq: str
    sample_fee: str
    customization: str
    delivery_time: str
    certifications: list
    keywords: list = None

@dataclass
class PriceQuote:
    """报价结果"""
    product: Product
    quantity: int
    unit_price: str
    total_price: str
    price_type: str  # "retail" | "wholesale" | "custom"
    notes: list

# ==================== 价格 Agent 核心逻辑 ====================

class KnowledgeBase:
    """知识库 - 商品价格信息"""

    def __init__(self, knowledge_data: dict):
        self.data = knowledge_data
        self._build_index()

    def _build_index(self):
        """构建关键词索引，加速匹配"""
        self.product_index = {}
        self._recursive_index(self.data)

    def _recursive_index(self, data):
        """递归遍历所有层级的列表，提取商品"""
        if isinstance(data, dict):
            for v in data.values():
                self._recursive_index(v)
        elif isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    keywords = item.get("keywords", [])
                    for kw in keywords:
                        self.product_index[kw.lower()] = item
                    # 如果这个 item 下面还有子列表，继续索引
                    for v in item.values():
                        self._recursive_index(v)

    def find_product(self, query: str) -> Optional[Product]:
        """根据查询词匹配商品"""
        query_lower = query.lower()

        # 精确匹配关键词
        for kw, item in self.product_index.items():
            if kw in query_lower or query_lower in kw:
                return self._item_to_product(item)

        # 模糊匹配商品名（递归搜索）
        return self._find_by_name(query_lower, self.data)

    def _find_by_name(self, query: str, data) -> Optional[Product]:
        """递归搜索商品名匹配"""
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
                        return self._item_to_product(item)
        return None

    def _item_to_product(self, item: dict) -> Product:
        return Product(
            name=item.get("name", ""),
            price_range=item.get("price_range", ""),
            moq=item.get("moq", ""),
            sample_fee=item.get("sample_fee", ""),
            customization=item.get("customization", ""),
            delivery_time=item.get("delivery_time", ""),
            certifications=item.get("certifications", []),
            keywords=item.get("keywords", [])
        )

    def get_all_products(self) -> list[Product]:
        """获取所有商品"""
        products = []
        for category, items in self.data.items():
            for item in items:
                products.append(self._item_to_product(item))
        return products


class PriceCalculator:
    """价格计算器"""

    @staticmethod
    def extract_quantity(text: str) -> tuple[Optional[int], str]:
        """从文本中提取数量"""
        # 匹配常见数量模式
        patterns = [
            r"(\d+)\s*(?:件|个|套|支|瓶|台|片)?\s*(?:起订|起)?",  # 50件起订
            r"起订[量]?\s*[：:]\s*(\d+)",  # 起订量：50
            r"要\s*(\d+)\s*(?:件|个|套|支|瓶|台)",  # 要50件
            r"(\d+)\s*(?:件|个|套|支|瓶|台|片)",  # 50件
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return int(match.group(1)), match.group(0)

        return None, ""

    @staticmethod
    def parse_price_range(price_range: str) -> tuple[float, float]:
        """解析价格区间，返回 (最低价, 最高价)"""
        # 匹配 ¥199-899 或 199-899
        match = re.search(r"[\¥\￥]?\s*(\d+(?:\.\d+)?)\s*[-~]\s*[\¥\￥]?(\d+(?:\.\d+)?)", price_range)
        if match:
            return float(match.group(1)), float(match.group(2))

        # 匹配单个价格 ¥199 或 199
        match = re.search(r"[\¥\￥]?\s*(\d+(?:\.\d+)?)", price_range)
        if match:
            price = float(match.group(1))
            return price, price

        return 0, 0

    @staticmethod
    def calculate_price(product: Product, quantity: int) -> PriceQuote:
        """根据数量计算价格"""
        min_price, max_price = PriceCalculator.parse_price_range(product.price_range)

        # 判断是定制还是标准品
        is_custom = "定制" in product.customization

        # 阶梯报价逻辑（示例）
        if quantity >= 1000:
            unit_price = min_price * 0.7  # 1000+ 件 7 折
            price_type = "bulk"
        elif quantity >= 500:
            unit_price = min_price * 0.8  # 500+ 件 8 折
            price_type = "wholesale"
        elif quantity >= 100:
            unit_price = min_price * 0.9  # 100+ 件 9 折
            price_type = "wholesale"
        elif quantity >= int(re.search(r"\d+", product.moq).group()) if re.search(r"\d+", product.moq) else False:
            unit_price = min_price  # MOQ 价格
            price_type = "moq"
        else:
            unit_price = min_price  # 零售价
            price_type = "retail"

        total_price = unit_price * quantity
        notes = []

        if price_type == "bulk":
            notes.append("大批量订单可申请更优价格")
        elif price_type == "wholesale":
            notes.append(f"订单 {quantity} 件享受批发价")
        elif price_type == "moq":
            notes.append(f"已达到起订量 {product.moq}")

        return PriceQuote(
            product=product,
            quantity=quantity,
            unit_price=f"¥{unit_price:.2f}",
            total_price=f"¥{total_price:.2f}",
            price_type=price_type,
            notes=notes
        )


class PriceReplyGenerator:
    """价格回复生成器"""

    def __init__(self):
        self.templates = {
            "greeting": "尊敬的买家，您好！感谢您的询价~",
            "quote_header": "📋 **您的报价如下**",
            "product_info": "**商品**：{product_name}",
            "price_table": """
| 数量 | 单价 | 合计 |
|------|------|------|
| {quantity}件 | {unit_price} | {total_price} |
""",
            "moq_note": "📦 **起订量**：{moq}",
            "delivery": "⏰ **交期**：{delivery_time}",
            "certifications": "✅ **认证**：{certs}",
            "customization": "🎨 **定制**：{custom}",
            "sample_note": "🧪 **样品**：{sample_fee}",
            "cta": """
💬 **下一步**：
- 点击「立即询价」获取实时价格
- 欢迎加我微信深入沟通，发送您的需求和数量，获取专属报价！
""",
            "closing": "期待与您合作，祝您生意兴隆！🎉"
        }

    def generate(self, inquiry_content: str, product: Product, quote: PriceQuote) -> str:
        """生成完整的价格回复"""

        parts = []

        # 问候
        parts.append(self.templates["greeting"])

        # 商品信息
        parts.append(self.templates["product_info"].format(product_name=product.name))

        # 价格表格
        price_table = self.templates["price_table"].format(
            quantity=quote.quantity,
            unit_price=quote.unit_price,
            total_price=quote.total_price
        )
        parts.append(self.templates["quote_header"])
        parts.append(price_table)

        # 附加信息
        parts.append(self.templates["moq_note"].format(moq=product.moq))
        parts.append(self.templates["delivery"].format(delivery_time=product.delivery_time))

        if product.certifications:
            certs = " / ".join(product.certifications)
            parts.append(self.templates["certifications"].format(certs=certs))

        # 样品信息
        if product.sample_fee:
            parts.append(self.templates["sample_note"].format(sample_fee=product.sample_fee))

        # 定制信息
        if product.customization:
            parts.append(self.templates["customization"].format(custom=product.customization))

        # 备注
        if quote.notes:
            parts.append("\n📝 **备注**：" + "；".join(quote.notes))

        # CTA
        parts.append(self.templates["cta"])

        # 结尾
        parts.append(self.templates["closing"])

        return "\n".join(parts)


class PriceExpertAgent:
    """价格专家 Agent"""

    def __init__(self):
        self.knowledge_base = KnowledgeBase(knowledge)
        self.reply_generator = PriceReplyGenerator()
        self.ai_config = AI_CONFIG

    async def call_ai(self, prompt: str) -> str:
        """调用 AI 生成内容"""
        provider = self.ai_config.get("provider", "hermes")

        if provider == "hermes":
            hermes_config = self.ai_config.get("hermes", {})
            api_url = hermes_config.get("api_url", "http://hermes:8000")
            api_key = hermes_config.get("api_key", "")

            payload = {
                "message": prompt,
                "api_key": api_key,
            }

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{api_url}/chat",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("reply", "")
                    else:
                        return None

        return None

    async def process(
        self,
        inquiry_content: str,
        buyer_name: str,
        product_name: str,
        context: dict = None
    ) -> dict:
        """
        处理价格询盘

        返回:
            {
                "success": bool,
                "product_match": Optional[Product],
                "quote": Optional[PriceQuote],
                "reply": str,
                "confidence": float,
                "requires_human": bool
            }
        """

        result = {
            "success": False,
            "product_match": None,
            "quote": None,
            "reply": "",
            "confidence": 0.0,
            "requires_human": False
        }

        # 1. 从知识库匹配商品
        product = self.knowledge_base.find_product(product_name)

        if not product:
            # 尝试用询盘全文匹配
            product = self.knowledge_base.find_product(inquiry_content)

        result["product_match"] = product

        if not product:
            # 未匹配到商品，使用通用回复
            result["reply"] = await self._generate_generic_reply(buyer_name, inquiry_content)
            result["confidence"] = 0.5
            result["requires_human"] = True
            return result

        # 2. 提取数量
        quantity, quantity_text = PriceCalculator.extract_quantity(inquiry_content)

        if not quantity:
            # 未明确数量，使用 MOQ
            moq_match = re.search(r"(\d+)", product.moq)
            quantity = int(moq_match.group(1)) if moq_match else 100

        # 3. 计算价格
        quote = PriceCalculator.calculate_price(product, quantity)
        result["quote"] = quote

        # 4. 生成回复
        reply = self.reply_generator.generate(inquiry_content, product, quote)

        # 5. AI 优化（如已配置）
        ai_reply = await self.call_ai(
            f"你是一个1688外贸业务员，买家[{buyer_name}]询价：{inquiry_content}\n\n"
            f"商品：{product.name}\n"
            f"报价：{quote.unit_price}/件 x {quantity}件 = {quote.total_price}\n"
            f"MOQ：{product.moq}\n"
            f"交期：{product.delivery_time}\n\n"
            f"请用专业、热情的语气生成一条回复，帮助买家做出购买决定。150字以内。"
        )

        if ai_reply:
            result["reply"] = ai_reply
        else:
            result["reply"] = reply

        # 6. 置信度评估
        result["confidence"] = self._calculate_confidence(product, quantity, quote)

        # 7. 高风险判断
        high_risk_keywords = REPLY_CONFIG.get("high_risk_keywords", [])
        for kw in high_risk_keywords:
            if kw in inquiry_content:
                result["requires_human"] = True
                break

        result["success"] = True
        return result

    async def _generate_generic_reply(self, buyer_name: str, inquiry_content: str) -> str:
        """生成通用回复（未匹配到商品时）"""
        return (
            f"尊敬的 {buyer_name}，您好！\n\n"
            f"感谢您的询价！\n\n"
            f"我们公司专业生产和销售户外运动、美妆护肤产品，"
            f"种类丰富，品质优良，价格有竞争力。\n\n"
            f"请问您具体想了解哪款产品？告诉我产品名称或型号，"
            f"我可以为您提供详细的报价单。\n\n"
            f"期待与您合作！"
        )

    def _calculate_confidence(self, product: Product, quantity: int, quote: PriceQuote) -> float:
        """计算回复置信度"""
        confidence = 0.7  # 基础分

        # 商品匹配精准度
        confidence += 0.15

        # 数量在合理范围
        if quantity >= int(re.search(r"\d+", product.moq).group()) if re.search(r"\d+", product.moq) else False:
            confidence += 0.1

        # 有完整的价格信息
        if quote.price_type != "retail":
            confidence += 0.05

        return min(confidence, 1.0)


# ==================== FastAPI 接口 ====================

price_expert = PriceExpertAgent()


class InquiryRequest(BaseModel):
    inquiry_content: str
    buyer_name: str
    product_name: str
    context: dict = None


@app.post("/process")
async def process_inquiry(request: InquiryRequest):
    """A2A 接口：处理价格询盘"""
    logger.info(f"处理价格询盘: {request.inquiry_content[:100]}")

    result = await price_expert.process(
        inquiry_content=request.inquiry_content,
        buyer_name=request.buyer_name,
        product_name=request.product_name,
        context=request.context
    )

    # 序列化 Product 和 Quote 对象
    response = {
        "success": result["success"],
        "confidence": result["confidence"],
        "requires_human": result["requires_human"],
        "reply": result["reply"],
        "product": None,
        "quote": None
    }

    if result["product_match"]:
        p = result["product_match"]
        response["product"] = {
            "name": p.name,
            "price_range": p.price_range,
            "moq": p.moq,
            "delivery_time": p.delivery_time,
            "certifications": p.certifications
        }

    if result["quote"]:
        q = result["quote"]
        response["quote"] = {
            "quantity": q.quantity,
            "unit_price": q.unit_price,
            "total_price": q.total_price,
            "price_type": q.price_type
        }

    return response


@app.get("/health")
async def health():
    return {"status": "ok", "agent": "price_expert"}


@app.get("/products")
async def list_products():
    """列出知识库中的所有商品"""
    products = price_expert.knowledge_base.get_all_products()
    return {
        "count": len(products),
        "products": [
            {
                "name": p.name,
                "price_range": p.price_range,
                "moq": p.moq,
                "keywords": p.keywords
            }
            for p in products
        ]
    }


@app.get("/.well-known/agent.json")
async def agent_card():
    """A2A Agent Card"""
    return {
        "name": "1688 Price Expert Agent",
        "description": "价格专家，处理价格咨询、报价生成、阶梯报价计算",
        "version": "1.0.0",
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "supportedTasks": ["price_quote", "price_consult"]
        },
        "endpoint": "http://price-expert:8003",
        "skills": ["price_calculation", "product_knowledge", "quotation"]
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)

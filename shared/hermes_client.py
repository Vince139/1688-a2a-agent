"""
Hermes 客户端封装
各 Expert Agent 通过这个模块调用 Hermes AI 能力
"""

import os
import logging
from typing import Optional
from dataclasses import dataclass

import yaml
import aiohttp

logger = logging.getLogger(__name__)


@dataclass
class HermesResponse:
    """Hermes 响应"""
    success: bool
    reply: str
    error: str = ""
    raw: dict = None


class HermesClient:
    """Hermes AI 客户端"""

    def __init__(self, config_path: str = None):
        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__),
                "config.yaml"
            )

        with open(config_path) as f:
            config = yaml.safe_load(f)

        ai_config = config.get("ai", {})
        hermes_config = ai_config.get("hermes", {})

        # Hermes API 配置
        self.api_url = hermes_config.get("api_url", "http://localhost:8000")
        self.api_key = hermes_config.get("api_key", "")
        self.model = hermes_config.get("model", "mini-max/M2.7")

        # Webhook 配置（用于接收1688消息）
        webhook_config = hermes_config.get("webhook", {})
        self.webhook_port = webhook_config.get("port", 8644)
        self.webhook_secret = webhook_config.get("secret", "")

    async def chat(
        self,
        message: str,
        system_prompt: str = None,
        context: dict = None,
        timeout: int = 60
    ) -> HermesResponse:
        """
        调用 Hermes 对话

        Args:
            message: 用户消息
            system_prompt: 系统提示词（可选）
            context: 额外上下文
            timeout: 超时秒数

        Returns:
            HermesResponse: AI 回复
        """

        payload = {
            "message": message,
            "model": self.model,
        }

        if system_prompt:
            payload["system_prompt"] = system_prompt

        if context:
            payload["context"] = context

        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.api_url}/chat",
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=timeout)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return HermesResponse(
                            success=True,
                            reply=data.get("reply", ""),
                            raw=data
                        )
                    else:
                        error_text = await resp.text()
                        return HermesResponse(
                            success=False,
                            reply="",
                            error=f"HTTP {resp.status}: {error_text}"
                        )

        except aiohttp.ClientConnectorError:
            return HermesResponse(
                success=False,
                reply="",
                error=f"无法连接到 Hermes ({self.api_url})"
            )
        except Exception as e:
            return HermesResponse(
                success=False,
                reply="",
                error=str(e)
            )

    async def generate_reply(
        self,
        inquiry_type: str,
        buyer_name: str,
        product_name: str,
        inquiry_content: str,
        product_info: dict = None,
        extra_context: dict = None
    ) -> HermesResponse:
        """
        生成 1688 询盘回复

        这是对 chat() 的封装，提供更简洁的接口
        """

        system_prompt = self._build_system_prompt(inquiry_type)
        message = self._build_user_message(
            buyer_name, product_name, inquiry_content, product_info
        )

        return await self.chat(message, system_prompt, extra_context)

    def _build_system_prompt(self, inquiry_type: str) -> str:
        """构建系统提示词"""

        base = """你是一个专业的1688平台外贸业务员。
风格：专业、热情、有亲和力
格式：中文回复，使用表情符号增加可读性
长度：150-300字
结构：问候 + 信息 + CTA"""

        type_prompts = {
            "price": """
你是价格专家，擅长：
- 根据数量给出阶梯报价
- 说明MOQ、交期、认证
- 提供性价比建议
- 引导买家下一步（加微信/安排样品）""",

            "sample": """
你是样品专家，擅长：
- 说明样品政策（费用、可退条件、流程）
- 消除买家顾虑（质量、运费、时间）
- 强调产品认证和品质保障
- 引导样品申请流程""",

            "customization": """
你是定制专家，擅长：
- 评估OEM/ODM可行性
- 说明定制MOQ和交期
- 讨论设计支持能力
- 引导深入沟通（加微信/视频会议）""",

            "general": """
你擅长回复各类1688询盘：
- 专业且有亲和力
- 提供有价值的信息
- 引导下一步行动"""
        }

        return base + type_prompts.get(inquiry_type, type_prompts["general"])

    def _build_user_message(
        self,
        buyer_name: str,
        product_name: str,
        inquiry_content: str,
        product_info: dict = None
    ) -> str:
        """构建用户消息"""

        msg = f"""买家：{buyer_name}
商品：{product_name}
询盘内容：{inquiry_content}"""

        if product_info:
            msg += "\n\n商品信息："
            if "price_range" in product_info:
                msg += f"\n- 价格区间：{product_info['price_range']}"
            if "moq" in product_info:
                msg += f"\n- MOQ：{product_info['moq']}"
            if "sample_fee" in product_info:
                msg += f"\n- 样品费：{product_info['sample_fee']}"
            if "delivery_time" in product_info:
                msg += f"\n- 交期：{product_info['delivery_time']}"
            if "certifications" in product_info:
                msg += f"\n- 认证：{product_info['certifications']}"

        msg += "\n\n请生成专业、有亲和力的回复。"

        return msg


class HermesA2AClient:
    """
    Hermes 作为 A2A 网络中的超级智囊
    用于当内置专家无法处理时，调用 Hermes 进行深度推理
    """

    def __init__(self, hermes_client: HermesClient = None):
        self.hermes = hermes_client or HermesClient()
        self.fallback_prompts = {
            "complex_negotiation": """
你是一个经验丰富的1688外贸谈判专家。买家正在谈判价格/付款方式。
分析买家的真实意图，识别可能的陷阱，给出谈判策略建议。
回复格式：
1. 买家意图分析
2. 风险评估
3. 建议的回复策略
4. 推荐的回复内容（50字以内）""",

            "complaint_handling": """
你是一个专业的1688客服。买家在投诉（质量/发货/服务）。
请生成一个得体、有诚意、有解决方案的回复。
回复格式：
1. 表示歉意
2. 说明情况
3. 解决方案
4. 预防措施""",

            "follow_up": """
你是一个专业的1688外贸业务员。买家询盘后没有回复。
请生成一个友好的跟进消息，提醒买家并表达合作诚意。
长度：100字以内，语气热情但不过度。"""
        }

    async def consult(
        self,
        scenario: str,
        context: dict
    ) -> HermesResponse:
        """
        就特定场景咨询 Hermes

        Args:
            scenario: 场景类型
            context: 相关上下文
        """

        if scenario not in self.fallback_prompts:
            scenario = "general"

        system = self.fallback_prompts[scenario]
        message = f"""
买家：{context.get('buyer_name', '未知')}
商品：{context.get('product_name', '未知')}
询盘内容：{context.get('inquiry_content', '未知')}
当前状态：{context.get('status', '初始')}
"""

        return await self.hermes.chat(message, system)


# ==================== 全局实例 ====================

_hermes_client = None


def get_hermes_client() -> HermesClient:
    """获取全局 Hermes 客户端实例"""
    global _hermes_client
    if _hermes_client is None:
        _hermes_client = HermesClient()
    return _hermes_client


def get_a2a_consultant() -> HermesA2AClient:
    """获取全局 A2A 顾问实例"""
    return HermesA2AClient(get_hermes_client())

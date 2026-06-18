"""
1688 多智能体系统 - Hermes 集成演示脚本

演示如何：
1. 直接调用 Hermes 生成回复
2. 通过 HermesA2AClient 进行场景咨询
3. 模拟完整的多智能体流程
"""

import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shared.hermes_client import HermesClient, HermesA2AClient


async def demo_direct_hermes():
    """演示1: 直接调用 Hermes"""
    print("\n" + "="*60)
    print("演示1: 直接调用 Hermes 生成回复")
    print("="*60)

    hermes = HermesClient()

    # 价格咨询
    print("\n--- 价格咨询 ---")
    result = await hermes.generate_reply(
        inquiry_type="price",
        buyer_name="张总",
        product_name="户外登山背包",
        inquiry_content="这个登山背包50件多少钱？有批发价吗？"
    )
    print(f"成功: {result.success}")
    print(f"回复:\n{result.reply}")

    # 样品申请
    print("\n--- 样品申请 ---")
    result = await hermes.generate_reply(
        inquiry_type="sample",
        buyer_name="李老板",
        product_name="瑜伽垫",
        inquiry_content="想申请瑜伽垫的样品，请问怎么收费？"
    )
    print(f"成功: {result.success}")
    print(f"回复:\n{result.reply}")


async def demo_hermes_consultant():
    """演示2: Hermes 场景咨询"""
    print("\n" + "="*60)
    print("演示2: Hermes 场景咨询 (A2A 协调)")
    print("="*60)

    consultant = HermesA2AClient()

    # 复杂谈判场景
    print("\n--- 场景: 复杂谈判 ---")
    result = await consultant.consult(
        scenario="complex_negotiation",
        context={
            "buyer_name": "王总",
            "product_name": "护肤套装",
            "inquiry_content": "你们价格太贵了，能不能再便宜点？另外账期能不能月结？",
            "status": "negotiation"
        }
    )
    print(f"成功: {result.success}")
    print(f"回复:\n{result.reply}")

    # 投诉处理场景
    print("\n--- 场景: 投诉处理 ---")
    result = await consultant.consult(
        scenario="complaint_handling",
        context={
            "buyer_name": "陈总",
            "product_name": "露营帐篷",
            "inquiry_content": "上次发的货有问题，帐篷的拉链是坏的，要求退货退款！",
            "status": "complaint"
        }
    )
    print(f"成功: {result.success}")
    print(f"回复:\n{result.reply}")


async def demo_full_flow_simulation():
    """演示3: 完整流程模拟"""
    print("\n" + "="*60)
    print("演示3: 完整询盘处理流程 (Hermes 增强)")
    print("="*60)

    hermes = HermesClient()

    # 模拟 Entry Agent 的分类
    inquiry_types = [
        {
            "content": "这个登山背包50件多少钱？有批发价吗？",
            "product": "户外登山背包",
            "buyer": "张总"
        },
        {
            "content": "想申请瑜伽垫的样品，请问怎么收费？多款可以一起拿吗？",
            "product": "瑜伽垫",
            "buyer": "李老板"
        },
        {
            "content": "我们品牌想贴牌生产护肤套装，5000件起，有设计稿",
            "product": "护肤套装",
            "buyer": "王总"
        }
    ]

    for i, inquiry in enumerate(inquiry_types, 1):
        print(f"\n{'='*40}")
        print(f"询盘 {i}: {inquiry['content'][:30]}...")
        print(f"{'='*40}")

        # 分类
        if any(kw in inquiry["content"] for kw in ["多少钱", "价格", "便宜", "批发"]):
            inquiry_type = "price"
        elif any(kw in inquiry["content"] for kw in ["样品", "样", "打样"]):
            inquiry_type = "sample"
        elif any(kw in inquiry["content"] for kw in ["定制", "贴牌", "品牌", "logo"]):
            inquiry_type = "customization"
        else:
            inquiry_type = "general"

        print(f"分类: {inquiry_type}")

        # 调用 Hermes
        result = await hermes.generate_reply(
            inquiry_type=inquiry_type,
            buyer_name=inquiry["buyer"],
            product_name=inquiry["product"],
            inquiry_content=inquiry["content"]
        )

        # 评估置信度（模拟）
        confidence = 0.85 if result.success else 0.3

        # 发送决策（模拟）
        if confidence > 0.7:
            decision = "✅ 自动发送"
        else:
            decision = "⏳ 需人工审核"

        print(f"置信度: {confidence:.0%}")
        print(f"决策: {decision}")
        print(f"\n回复预览:\n{result.reply[:200]}...")


async def demo_system_info():
    """演示4: 系统信息"""
    print("\n" + "="*60)
    print("系统信息")
    print("="*60)

    hermes = HermesClient()
    print(f"Hermes API: {hermes.api_url}")
    print(f"Hermes Model: {hermes.model}")
    print(f"Webhook Port: {hermes.webhook_port}")


async def main():
    print("""
╔══════════════════════════════════════════════════════════════╗
║       1688 多智能体系统 - Hermes 集成演示                      ║
║                                                              ║
║  本演示展示如何将 Hermes AI 集成到多智能体系统中               ║
║  作为各 Expert Agent 的 AI 能力后端                          ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # 先测试连接
    print("测试 Hermes 连接...")
    hermes = HermesClient()

    test_result = await hermes.chat(
        message="请回复'连接正常'",
        system_prompt="你是一个测试助手"
    )

    if test_result.success:
        print(f"✅ Hermes 连接正常: {test_result.reply}")
    else:
        print(f"❌ Hermes 连接失败: {test_result.error}")
        print("\n请确保 Hermes 正在运行并可访问。")
        print("启动 Hermes: hermes gateway run")
        return

    await demo_system_info()
    await demo_direct_hermes()
    await demo_hermes_consultant()
    await demo_full_flow_simulation()

    print("\n" + "="*60)
    print("演示完成!")
    print("="*60)

    print("""
下一步：
1. 启动完整的 Docker 环境: cd docker && docker-compose up -d
2. 配置 1688 API 凭证: 编辑 shared/config.yaml
3. 配置企业微信通知: 设置 notification.wecom_webhook
4. 测试完整流程: python test_agents.py

详细说明请参考: HERMES_INTEGRATION.md
""")


if __name__ == "__main__":
    asyncio.run(main())

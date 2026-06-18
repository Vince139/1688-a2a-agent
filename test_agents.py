"""
1688 多智能体系统 - 测试脚本
测试各 Agent 的基本功能
"""

import asyncio
import json
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


async def test_price_expert():
    """测试价格专家"""
    print("\n" + "="*60)
    print("测试 Price Expert Agent")
    print("="*60)

    try:
        import httpx
        from agents.price_expert.agent import price_expert

        test_cases = [
            {
                "inquiry_content": "这个登山背包50件多少钱？",
                "buyer_name": "张总",
                "product_name": "户外登山背包"
            },
            {
                "inquiry_content": "瑜伽垫怎么卖？100套有优惠吗？",
                "buyer_name": "李老板",
                "product_name": "瑜伽垫"
            }
        ]

        for i, case in enumerate(test_cases, 1):
            print(f"\n--- 测试用例 {i} ---")
            print(f"询盘: {case['inquiry_content']}")

            result = await price_expert.process(
                inquiry_content=case["inquiry_content"],
                buyer_name=case["buyer_name"],
                product_name=case["product_name"]
            )

            print(f"成功: {result['success']}")
            print(f"置信度: {result['confidence']:.2f}")
            print(f"需人工: {result['requires_human']}")
            if result.get("quote"):
                q = result["quote"]
                print(f"报价: {q['quantity']}件 x {q['unit_price']} = {q['total_price']}")
            print(f"回复预览: {result['reply'][:150]}...")

    except ImportError as e:
        print(f"导入失败（服务未启动）: {e}")
    except Exception as e:
        print(f"测试失败: {e}")


async def test_sample_expert():
    """测试样品专家"""
    print("\n" + "="*60)
    print("测试 Sample Expert Agent")
    print("="*60)

    try:
        from agents.sample_expert.agent import sample_expert

        test_cases = [
            {
                "inquiry_content": "我想申请帐篷的样品，请问怎么收费？",
                "buyer_name": "王总",
                "product_name": "露营帐篷"
            },
            {
                "inquiry_content": "口红能拿样品吗？多款一起拿可以吗？",
                "buyer_name": "陈采购",
                "product_name": "口红"
            }
        ]

        for i, case in enumerate(test_cases, 1):
            print(f"\n--- 测试用例 {i} ---")
            print(f"询盘: {case['inquiry_content']}")

            result = await sample_expert.process(
                inquiry_content=case["inquiry_content"],
                buyer_name=case["buyer_name"],
                product_name=case["product_name"]
            )

            print(f"成功: {result['success']}")
            print(f"置信度: {result['confidence']:.2f}")
            print(f"需人工: {result['requires_human']}")
            if result.get("policies"):
                p = result["policies"][0]
                print(f"样品费: {p['sample_fee']}")
                print(f"可退: {p['sample_fee_refundable']}")
            print(f"回复预览: {result['reply'][:150]}...")

    except ImportError as e:
        print(f"导入失败（服务未启动）: {e}")
    except Exception as e:
        print(f"测试失败: {e}")


async def test_customize_expert():
    """测试定制专家"""
    print("\n" + "="*60)
    print("测试 Customize Expert Agent")
    print("="*60)

    try:
        from agents.customize_expert.agent import custom_expert

        test_cases = [
            {
                "inquiry_content": "我们品牌想贴牌生产瑜伽服，1000件起，有设计稿",
                "buyer_name": "赵总",
                "product_name": "瑜伽服"
            },
            {
                "inquiry_content": "背包能印我们公司的logo吗？数量大概500件",
                "buyer_name": "周经理",
                "product_name": "户外登山背包"
            }
        ]

        for i, case in enumerate(test_cases, 1):
            print(f"\n--- 测试用例 {i} ---")
            print(f"询盘: {case['inquiry_content']}")

            result = await custom_expert.process(
                inquiry_content=case["inquiry_content"],
                buyer_name=case["buyer_name"],
                product_name=case["product_name"]
            )

            print(f"成功: {result['success']}")
            print(f"置信度: {result['confidence']:.2f}")
            print(f"需人工: {result['requires_human']}")
            if result.get("assessment"):
                a = result["assessment"]
                print(f"可行性: {a['feasibility']}")
                print(f"需确认: {a['needs_discussion'][:2] if a.get('needs_discussion') else '无'}")
            print(f"回复预览: {result['reply'][:150]}...")

    except ImportError as e:
        print(f"导入失败（服务未启动）: {e}")
    except Exception as e:
        print(f"测试失败: {e}")


async def test_aggregator():
    """测试聚合器"""
    print("\n" + "="*60)
    print("测试 Aggregator Agent")
    print("="*60)

    try:
        from agents.aggregator.agent import aggregator, InquiryContext, InquiryType

        context = InquiryContext(
            inquiry_id="TEST-001",
            buyer_id="B001",
            buyer_name="测试买家",
            product_name="登山背包",
            product_category="户外",
            inquiry_content="这个背包50件多少钱？",
            inquiry_type=InquiryType.PRICE,
            timestamp="2024-01-01T10:00:00"
        )

        expert_responses = [
            {
                "expert_name": "price_expert",
                "success": True,
                "reply": "尊敬的买家，您好！登山背包50件报价如下：¥89/件，合计¥4450，交期7-15天。",
                "confidence": 0.9,
                "requires_human": False
            }
        ]

        result = await aggregator.aggregate(context, expert_responses)

        print(f"聚合成功: {result.success}")
        print(f"回复类型: {result.reply_type}")
        print(f"置信度: {result.confidence:.2f}")
        print(f"风险等级: {result.risk_level.value}")
        print(f"需人工: {result.requires_human}")
        print(f"最终回复: {result.final_reply[:150]}...")

    except ImportError as e:
        print(f"导入失败（服务未启动）: {e}")
    except Exception as e:
        print(f"测试失败: {e}")


async def test_exit_agent():
    """测试出口Agent"""
    print("\n" + "="*60)
    print("测试 Exit Agent")
    print("="*60)

    try:
        from agents.exit.agent import exit_agent

        # 测试自动发送场景
        print("\n--- 场景1: 满足自动发送条件 ---")
        result = await exit_agent.process(
            inquiry_id="TEST-001",
            buyer_id="B001",
            buyer_name="测试买家",
            product_name="登山背包",
            inquiry_content="背包50件多少钱？",
            final_reply="您好！50件报价如下...",
            inquiry_type="price",
            confidence=0.9,
            requires_human=False,
            risk_level="low",
            risk_flags=[]
        )
        print(f"结果: {result.result.value}")
        print(f"消息: {result.message}")

        # 测试人工队列场景
        print("\n--- 场景2: 需要人工审核 ---")
        result = await exit_agent.process(
            inquiry_id="TEST-002",
            buyer_id="B002",
            buyer_name="新买家",
            product_name="瑜伽服",
            inquiry_content="想贴牌生产，有设计稿，1000件起",
            final_reply="您好！贴牌定制需要详细沟通...",
            inquiry_type="customization",
            confidence=0.7,
            requires_human=True,
            risk_level="medium",
            risk_flags=["专家标记需人工:customize_expert"]
        )
        print(f"结果: {result.result.value}")
        print(f"消息: {result.message}")

    except ImportError as e:
        print(f"导入失败（服务未启动）: {e}")
    except Exception as e:
        print(f"测试失败: {e}")


async def test_full_flow():
    """测试完整流程（模拟）"""
    print("\n" + "="*60)
    print("测试完整流程（模拟）")
    print("="*60)

    print("""
完整流程说明：

1. Entry Agent 接收 1688 webhook
   POST /1688/webhook
   ↓
2. 发送给 Classifier Agent 分类
   type = "price" | "sample" | "customization"
   ↓
3. 根据类型路由到对应 Expert Agent
   price     → Price Expert
   sample    → Sample Expert
   customize → Customize Expert
   ↓
4. Expert 回复汇聚到 Aggregator
   聚合 + 质量检查 + 风险评估
   ↓
5. Aggregator 结果发给 Exit Agent
   判断: 自动发送 or 人工队列
   ↓
6a. 自动发送 → 1688 API
   或
   6b. 人工审核 → 企业微信通知 → 销售跟进
    """)


async def main():
    print("="*60)
    print("1688 多智能体系统 - Agent 测试")
    print("="*60)

    await test_price_expert()
    await test_sample_expert()
    await test_customize_expert()
    await test_aggregator()
    await test_exit_agent()
    await test_full_flow()

    print("\n" + "="*60)
    print("测试完成！")
    print("="*60)


if __name__ == "__main__":
    asyncio.run(main())

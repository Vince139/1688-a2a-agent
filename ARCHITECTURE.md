# 1688 智能询盘多智能体系统 - 基于 A2A 协议

## 系统架构图

```
                           ┌─────────────────────┐
                           │   1688 开放平台      │
                           │  (买家询盘 / 回复)   │
                           └──────────┬──────────┘
                                      │
                                      ▼
                    ┌────────────────────────────────┐
                    │      Entry Agent (入口)        │
                    │  • 接收 1688 询盘 webhook       │
                    │  • 解析消息结构                 │
                    │  • 提取买家信息 + 询盘内容       │
                    │  • 向 Agent Card 注册自身       │
                    └───────────────┬────────────────┘
                                    │ A2A SendMessage
                                    ▼
                    ┌────────────────────────────────┐
                    │   Classifier Agent (分类器)     │
                    │  • 分析询盘类型                 │
                    │  • 判断意图（价格/样品/定制/其他）│
                    │  • 评估置信度                   │
                    │  • 决定路由                     │
                    └───────────┬────────────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
          ▼                     ▼                     ▼
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  Price Expert    │  │ Sample Expert   │  │Customize Expert │
│  (价格专家)       │  │ (样品专家)       │  │  (定制专家)      │
│                  │  │                 │  │                 │
│  • 查知识库价格   │  │  • 样品政策     │  │  • OEM/ODM 能力  │
│  • 生成报价单     │  │  • 样品费用     │  │  • MOQ 判断     │
│  • 议价处理       │  │  • 退样政策     │  │  • 交期评估      │
│  • 阶梯报价       │  │  • 快递安排     │  │  • 特殊工艺      │
└────────┬────────┘  └────────┬────────┘  └────────┬────────┘
         │                     │                     │
         └─────────────────────┼─────────────────────┘
                               │ A2A Responses
                               ▼
                    ┌────────────────────────────────┐
                    │   Aggregator Agent (聚合器)     │
                    │  • 汇总各专家回复               │
                    │  • 生成统一专业回复             │
                    │  • 信任级别判断                 │
                    │  • 决定自动发送 or 人工审核     │
                    └───────────────┬────────────────┘
                                    │
                                    ▼
                    ┌────────────────────────────────┐
                    │   Exit Agent (出口)             │
                    │  • 自动发送：调用1688 API回复   │
                    │  • 人工审核：推送到人工队列      │
                    │  • 异常处理：记录并告警          │
                    └────────────────────────────────┘
```

## Agent Card 注册表

```json
{
  "name": "1688 Inquiry Entry Agent",
  "description": "1688平台询盘接收入口Agent",
  "version": "1.0.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": true,
    "supportedTasks": ["inquiry_receive"]
  },
  "endpoint": "http://entry-agent:8001",
  "skills": ["inquiry_parse", "webhook_receive"]
}
```

## 任务流程示例

### 询盘：价格咨询

```
买家 → "这个登山背包多少钱？50件起订价格多少？"
    │
    ▼
Entry Agent → 解析消息，提取关键词
    │
    ▼
Classifier Agent → type="price", confidence=0.92
    │
    ▼
Price Expert Agent → 
  知识库匹配 → 登山背包 ¥89-399, 30件起订, ¥89/件
  生成报价建议
    │
    ▼
Aggregator Agent → 
  组装回复：感谢+产品参数+MOQ+价格区间+引导加微信
  置信度 92% > 阈值 85% → 自动发送
    │
    ▼
Exit Agent → 调用1688 API发送回复
```

### 询盘：样品申请

```
买家 → "我想申请3款露营帐篷的样品，请问怎么操作？"
    │
    ▼
Entry Agent → 解析消息
    │
    ▼
Classifier Agent → type="sample", confidence=0.95
    │
    ▼
Sample Expert Agent →
  知识库 → 露营帐篷样品费 ¥100-400/件，可退
  判断：多款样品需确认具体型号
  生成回复：介绍样品流程 + 需买家确认型号 + 样品费说明
    │
    ▼
Aggregator Agent → 置信度 95% > 85% → 自动发送
    │
    ▼
Exit Agent → 发送回复
```

### 询盘：定制/OEM（需人工）

```
买家 → "我们品牌想贴牌生产瑜伽服，有设计和包装方案，1000件起订"
    │
    ▼
Classifier Agent → type="customization", confidence=0.88
    │
    ▼
Customize Expert Agent →
  识别：完整定制需求（设计稿+包装+MOQ 1000件）
  评估：涉及品牌logo/包装，需人工确认细节
  → requires_human=true
    │
    ▼
Aggregator Agent → 置信度 88%，但 requires_human=true
    │
    ▼
Exit Agent → 推送到人工审核队列 + 企业微信通知
```

## 目录结构

```
1688-agent-a2a/
├── agents/                    # 各 Agent 实现
│   ├── entry/                 # 入口 Agent
│   │   ├── __init__.py
│   │   ├── agent.py           # Entry Agent 主逻辑
│   │   └── agentcard.json     # Agent Card
│   ├── classifier/            # 分类器 Agent
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   └── agentcard.json
│   ├── price_expert/          # 价格专家
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   └── agentcard.json
│   ├── sample_expert/         # 样品专家
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   └── agentcard.json
│   ├── customize_expert/      # 定制专家
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   └── agentcard.json
│   ├── aggregator/            # 聚合器 Agent
│   │   ├── __init__.py
│   │   ├── agent.py
│   │   └── agentcard.json
│   └── exit/                  # 出口 Agent
│       ├── __init__.py
│       ├── agent.py
│       └── agentcard.json
├── shared/                     # 共享模块
│   ├── a2a_client.py          # A2A 客户端封装
│   ├── agent_card.py          # Agent Card 模型
│   ├── task_state.py          # 任务状态定义
│   └── config.py              # 配置管理
├── knowledge_base/             # 知识库
│   ├── products.yaml          # 商品知识库
│   └── templates.py            # 回复模板
├── tests/                      # 测试
│   ├── test_classifier.py
│   ├── test_price_expert.py
│   └── test_full_flow.py
├── docker/                    # Docker 部署
│   ├── docker-compose.yaml
│   └── Dockerfile.agent
├── a2a_server.py               # A2A Server 入口（各 Agent 共用）
└── README.md
```

## 技术选型

| 组件 | 技术方案 |
|------|---------|
| A2A 协议 | a2a-python SDK |
| 框架基础 | FastAPI / uvicorn |
| Agent 实现 | 可混用：LangGraph / AutoGen / 直接实现 |
| 知识库 | YAML + Python Pydantic 模型 |
| 消息队列 | Redis (长任务协作) |
| 部署 | Docker Compose |
| 1688 API | top-sdk (阿里官方) |

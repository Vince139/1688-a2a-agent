# 1688 多智能体系统 + Hermes 集成指南

## 系统架构

```
                         ┌──────────────────┐
                         │   1688 平台       │
                         │  (买家询盘/通知)   │
                         └────────┬─────────┘
                                  │ POST /1688/webhook
                                  ▼
                         ┌──────────────────┐
                         │   Nginx / 防火墙  │
                         │   (端口 8080)     │
                         └────────┬─────────┘
                                  │
                                  ▼
                         ┌──────────────────┐
                         │  Entry Agent      │
                         │  (端口 8001)      │
                         │  - 消息解析       │
                         │  - 分类路由       │
                         └────────┬─────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                   │                   │
              ▼                   ▼                   ▼
    ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
    │  Price Expert   │ │ Sample Expert   │ │Customize Expert │
    │  (端口 8003)    │ │ (端口 8004)     │ │ (端口 8005)     │
    └────────┬────────┘ └────────┬────────┘ └────────┬────────┘
              │                   │                   │
              └───────────────────┼───────────────────┘
                                  │ A2A 协议
                                  ▼
                         ┌──────────────────┐
                         │  Aggregator       │
                         │  (端口 8006)      │
                         │  - 质量检查       │
                         │  - 风险评估       │
                         └────────┬─────────┘
                                  │
                                  ▼
                         ┌──────────────────┐
                         │  Exit Agent       │
                         │  (端口 8007)      │
                         │  - 发送决策       │
                         │  - 人工队列       │
                         └────────┬─────────┘
                                  │
              ┌───────────────────┼───────────────────┐
              │                                           │
              ▼                                           ▼
    ┌─────────────────┐                    ┌─────────────────┐
    │  1688 API       │                    │  企业微信通知    │
    │  (自动发送回复)  │                    │  (人工审核提醒)  │
    └─────────────────┘                    └─────────────────┘

    ┌─────────────────────────────────────────────────────┐
    │              Hermes (外部 AI 协调者)                 │
    │  - 为 Expert Agent 提供 AI 能力                     │
    │  - 复杂谈判、投诉处理等场景兜底                     │
    │  - 监控/管理员通知                                   │
    └─────────────────────────────────────────────────────┘
```

## Hermes 集成方式

### 方式 1: Hermes Webhook（推荐用于 1688）

```bash
# 1. 在 Hermes 中创建 webhook 订阅
hermes webhook subscribe 1688-inquiry \
  --events "inquiry.received" \
  --prompt "处理1688询盘：{payload.content}" \
  --deliver origin \
  --description "1688询盘处理"

# 2. 返回 webhook URL
# https://your-server:8644/webhooks/1688-inquiry

# 3. 配置 1688 开放平台使用该 URL
```

### 方式 2: Hermes 作为 Expert Agent 的 AI 后端

每个 Expert Agent 内置调用 Hermes 的能力：

```python
# Expert Agent 内部
from shared.hermes_client import HermesClient

hermes = HermesClient()

# 当内置逻辑无法处理时，调用 Hermes
result = await hermes.generate_reply(
    inquiry_type="price",
    buyer_name="张总",
    product_name="登山背包",
    inquiry_content="50件多少钱？"
)
```

### 方式 3: Hermes 作为 A2A 网络的超级协调者

```python
# HermesA2AClient - 当多智能体流程失败时使用
from shared.hermes_client import HermesA2AClient

consultant = HermesA2AClient()

# 复杂场景咨询
result = await consultant.consult(
    scenario="complex_negotiation",
    context={
        "buyer_name": "张总",
        "inquiry_content": "你们价格太贵了，能不能再便宜点？账期能不能月结？"
    }
)
```

## 部署步骤

### 1. 准备环境

```bash
# 安装 Docker 和 Docker Compose
sudo apt update
sudo apt install docker.io docker-compose

# 启动 Docker
sudo systemctl start docker
sudo systemctl enable docker
```

### 2. 配置

编辑 `shared/config.yaml`：

```yaml
# AI 配置 - Hermes
ai:
  provider: "hermes"
  hermes:
    api_url: "http://localhost:8000"  # Hermes API 地址
    api_key: ""                       # Hermes API Key
    model: "mini-max/M2.7"

# 1688 API 配置
aliyun:
  app_key: "your_app_key"
  app_secret: "your_app_secret"
  refresh_token: "your_refresh_token"

# 企业微信通知
notification:
  wecom_webhook: "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxx"

# 回复控制
reply:
  auto_reply_threshold: 85
  auto_reply_types:
    - price
    - sample
    - logistics
    - quality
  high_risk_keywords:
    - 仿牌
    - 高仿
    - 账期
    - 赊账
```

### 3. 启动系统

```bash
cd /home/vince/1688-agent-a2a/docker

# 启动所有服务
docker-compose up -d

# 查看日志
docker-compose logs -f entry-agent

# 查看所有服务状态
docker-compose ps
```

### 4. 测试

```bash
# 测试 Webhook 端点
curl -X POST http://localhost:8080/1688/webhook \
  -H "Content-Type: application/json" \
  -d '{
    "messageId": "TEST-001",
    "buyerId": "B001",
    "buyerName": "测试买家",
    "productName": "登山背包",
    "content": "这个背包50件多少钱？"
  }'

# 预期响应
{
  "success": true,
  "inquiry_id": "TEST-001",
  "reply_type": "auto",
  "confidence": 0.9,
  "reply_preview": "尊敬的买家，您好！..."
}
```

### 5. 配置 1688 开放平台

1. 登录 1688 开放平台
2. 创建应用，获取 AppKey 和 AppSecret
3. 配置消息订阅，选择"询盘消息"类型
4. 填写 Webhook URL: `http://your-server:8080/1688/webhook`

## Hermes 监控面板

### 查看任务状态

```bash
# 列出最近任务
curl http://localhost:8080/tasks | jq

# 查看特定任务
curl http://localhost:8080/tasks/TASK-xxx | jq
```

### 查看人工队列

```bash
# 查看待处理队列
curl http://localhost:8080/exit/queue | jq

# 批准发送
curl -X POST http://localhost:8080/exit/queue/HR-xxx/approve

# 编辑后发送
curl -X POST http://localhost:8080/exit/queue/HR-xxx/edit \
  -H "Content-Type: application/json" \
  -d '{"edited_reply": "修改后的回复内容..."}'
```

### Prometheus 监控

访问 `http://localhost:9090` 查看 Prometheus 监控面板。

## 故障排查

### 服务无法启动

```bash
# 查看日志
docker-compose logs entry-agent

# 常见问题
# 1. 端口被占用：修改 docker-compose.yaml 中的端口映射
# 2. 配置文件错误：检查 shared/config.yaml 语法
# 3. 权限问题：确保 Docker 有权限访问共享目录
```

### 询盘处理失败

```bash
# 1. 检查 Hermes 连接
curl http://localhost:8001/hermes/test

# 2. 检查 Agent 通信
curl http://localhost:8001/health
curl http://localhost:8003/health  # price expert

# 3. 查看任务详情
curl http://localhost:8001/tasks | jq '.tasks[-1]'
```

### 企业微信通知失败

```bash
# 检查 webhook 配置
grep wecom config.yaml

# 测试 webhook
curl -X POST "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{"msgtype": "text", "text": {"content": "测试消息"}}'
```

## 安全建议

1. **API 密钥保管**
   - 不要将密钥提交到 Git
   - 使用环境变量或 Docker Secrets

2. **网络隔离**
   - 内部 Agent 网络不暴露到公网
   - 只暴露 Nginx 端口 (8080)

3. **限流**
   - Nginx 配置请求限流
   - 1688 开放平台配置接口调用频率

4. **日志审计**
   - 定期检查日志
   - 记录所有人工操作

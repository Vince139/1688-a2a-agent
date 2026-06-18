"""
Webhook 代理 - 解决 HMAC 签名问题
1688 → 本代理(无签名) → Hermes Webhook
"""

import os
import json
import logging
import asyncio
import aiohttp
from aiohttp import web

import hmac
import hashlib

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Hermes 配置
HERMES_URL = "http://localhost:8644"
HERMES_SECRET = "YVSM4jhOsdLL_gpyh17O2TfRkJ7_jMGnIKKRzzB6Vks"  # 当前订阅的 secret
WEBHOOK_PATH = "/webhooks/1688-inquiry"

# 本地端口
LOCAL_PORT = 8645


async def forward_to_hermes(payload: dict) -> dict:
    """转发请求到 Hermes，带正确签名"""

    # 直接转发原始 payload，不包装 event
    payload_json = json.dumps(payload, ensure_ascii=False)

    # 计算 HMAC 签名
    signature = hmac.new(
        HERMES_SECRET.encode(),
        payload_json.encode(),
        hashlib.sha256
    ).hexdigest()

    # 发送请求
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{HERMES_URL}{WEBHOOK_PATH}",
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": f"sha256={signature}"
            },
            data=payload_json
        ) as resp:
            return await resp.json()


async def handle_1688_webhook(request: web.Request) -> web.Response:
    """接收 1688 回调（无签名要求）"""

    try:
        payload = await request.json()
    except Exception as e:
        logger.error(f"JSON 解析失败: {e}")
        return web.json_response({"error": "Invalid JSON"}, status=400)

    logger.info(f"收到 1688 回调: {payload.get('buyer_name', 'Unknown')}")

    # 转发给 Hermes
    try:
        result = await forward_to_hermes(payload)
        logger.info(f"Hermes 响应: {result}")
        return web.json_response({"status": "ok", "hermes": result})
    except Exception as e:
        logger.error(f"转发失败: {e}")
        return web.json_response({"status": "error", "error": str(e)}, status=500)


async def handle_test(request: web.Request) -> web.Response:
    """测试端点"""
    return web.json_response({
        "status": "ok",
        "service": "1688 Webhook Proxy",
        "hermes_url": HERMES_URL,
        "local_port": LOCAL_PORT
    })


app = web.Application()
app.router.add_post("/1688/webhook", handle_1688_webhook)
app.router.add_get("/health", handle_test)

if __name__ == "__main__":
    logger.info(f"启动 Webhook 代理，监听端口 {LOCAL_PORT}")
    logger.info(f"转发到: {HERMES_URL}{WEBHOOK_PATH}")
    web.run_app(app, host="0.0.0.0", port=LOCAL_PORT)

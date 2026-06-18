"""
A2A 客户端封装 - 用于 Agent 之间互相通信
使用原生 httpx 实现，不依赖 a2a-sdk 版本
"""

import os
import asyncio
import logging
from typing import Optional, Any

import httpx

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class A2AClientManager:
    """A2A 客户端管理器 - 负责发现和连接各 Agent"""

    def __init__(self):
        self.agents: dict[str, str] = {}
        self._client: Optional[httpx.AsyncClient] = None

    def register(self, name: str, url: str):
        """注册一个 Agent"""
        self.agents[name] = url
        logger.info(f"注册 Agent: {name} -> {url}")

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None

    async def get_agent_card(self, name: str) -> Optional[dict]:
        """获取指定 Agent 的 Agent Card"""
        url = self.agents.get(name)
        if not url:
            return None

        try:
            resp = await self.client.get(f"{url}/.well-known/agent.json")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"获取 Agent Card 失败 {name}: {e}")
        return None

    async def send_message(
        self,
        agent_name: str,
        payload: dict[str, Any]
    ) -> dict[str, Any]:
        """向指定 Agent 发送消息"""
        url = self.agents.get(agent_name)
        if not url:
            return {"error": f"Agent {agent_name} 未注册"}

        try:
            resp = await self.client.post(f"{url}/classify", json=payload)
            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            else:
                return {"error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            logger.error(f"发送消息到 {agent_name} 失败: {e}")
            return {"error": str(e)}

    async def send_task(
        self,
        agent_name: str,
        endpoint: str,
        payload: dict[str, Any]
    ) -> dict[str, Any]:
        """向指定 Agent 发送任务请求"""
        url = self.agents.get(agent_name)
        if not url:
            return {"error": f"Agent {agent_name} 未注册"}

        try:
            resp = await self.client.post(f"{url}/{endpoint}", json=payload)
            if resp.status_code == 200:
                return {"success": True, "data": resp.json()}
            else:
                return {"error": f"HTTP {resp.status_code}: {resp.text}"}
        except Exception as e:
            logger.error(f"发送任务到 {agent_name}/{endpoint} 失败: {e}")
            return {"error": str(e)}

    async def broadcast(
        self,
        endpoint: str,
        payload: dict[str, Any]
    ) -> dict[str, dict[str, Any]]:
        """广播请求给所有已注册的 Agent"""
        tasks = {}
        for name in self.agents:
            tasks[name] = self.send_task(name, endpoint, payload)

        results = await asyncio.gather(**tasks, return_exceptions=True)
        return dict(zip(self.agents.keys(), results))


# 全局客户端管理器实例
a2a_client = A2AClientManager()

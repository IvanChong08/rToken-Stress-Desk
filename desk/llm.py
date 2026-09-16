"""
大模型客户端 (OpenAI 兼容接口)。

大模型在本项目里只做三件事: 理解交易想法、编排要调用哪些分析、根据工具结果写结论。
它【不产生任何数字】—— 结论里的数字只能来自带来源记录的数据调用。

配置 (环境变量, key 只放在环境变量里, 不写进代码或配置文件):
  LLM_PROVIDER          qwen | gemini (默认 qwen)
  BITGET_QWEN_API_KEY   Bitget 黑客松发放的 Qwen 额度 (手册里用的变量名; 也兼容 QWEN_API_KEY)
  GEMINI_API_KEY        Google AI Studio 的 key (备用)
  LLM_MODEL             可选, 覆盖默认模型名

接口: 先用 /chat/completions; 若返回 404/405 (不支持), 自动改用 /responses
(手册里 Codex 的配置写的是 wire_api = "responses", Cursor 走的是 OpenAI Base URL 覆盖)。
key 只出现在 Authorization 请求头里, 不写入任何日志。
"""
from __future__ import annotations

import os
import time

import requests

from .provenance import Provenance

PROVIDERS = {
    # 来源: 黑客松开发者手册 V 章 Qwen Setup Guide (Base URL + 推荐模型)
    "qwen": {"base_url": "https://hackathon.bitgetops.com/v1", "model": "qwen3.8-max",
             "key_envs": ["BITGET_QWEN_API_KEY", "QWEN_API_KEY"]},
    # Gemini 的 OpenAI 兼容端点; 默认模型名可能随时间变化, 用 LLM_MODEL 覆盖
    "gemini": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash",
               "key_envs": ["GEMINI_API_KEY"]},
}


def _key(provider: str) -> str | None:
    for name in PROVIDERS[provider]["key_envs"]:
        if os.environ.get(name):
            return os.environ[name]
    return None


def available_provider() -> str | None:
    """按 LLM_PROVIDER 优先, 否则返回第一个配置了 key 的提供方; 都没有返回 None。"""
    preferred = os.environ.get("LLM_PROVIDER", "qwen")
    for p in [preferred] + [x for x in PROVIDERS if x != preferred]:
        if p in PROVIDERS and _key(p):
            return p
    return None


def _responses_text(j: dict) -> str:
    if j.get("output_text"):
        return j["output_text"]
    parts = []
    for item in j.get("output", []):
        for c in item.get("content", []) or []:
            if c.get("type") in ("output_text", "text") and c.get("text"):
                parts.append(c["text"])
    return "".join(parts)


def chat(messages: list[dict], provider: str | None = None, temperature: float = 0.0,
         timeout: float = 60.0, json_mode: bool = False):
    """返回 (回复文本 | None, Provenance)。没有可用 key 时直接返回失败记录, 不抛异常。"""
    provider = provider or available_provider()
    t0 = time.time()
    if provider is None:
        return None, Provenance("llm", "none", {}, time.time(), 0, False, error="no LLM API key configured")
    cfg = PROVIDERS[provider]
    model = os.environ.get("LLM_MODEL", cfg["model"])
    headers = {"Authorization": "Bearer " + _key(provider)}
    base = cfg["base_url"].rstrip("/")
    params = {"provider": provider, "model": model, "json_mode": json_mode}
    try:
        body = {"model": model, "messages": messages, "temperature": temperature}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = requests.post(base + "/chat/completions", json=body, headers=headers, timeout=timeout)
        if r.status_code in (404, 405):
            params["api"] = "responses"
            body = {"model": model, "input": messages, "temperature": temperature}
            if json_mode:
                body["text"] = {"format": {"type": "json_object"}}
            r = requests.post(base + "/responses", json=body, headers=headers, timeout=timeout)
            r.raise_for_status()
            text = _responses_text(r.json())
        else:
            params["api"] = "chat.completions"
            r.raise_for_status()
            text = r.json()["choices"][0]["message"]["content"]
        return text, Provenance("llm", model, params, time.time(), int((time.time() - t0) * 1000), True)
    except Exception as e:
        msg = "%s: %s" % (type(e).__name__, str(e)[:200])
        return None, Provenance("llm", model, params, time.time(), int((time.time() - t0) * 1000), False, error=msg)

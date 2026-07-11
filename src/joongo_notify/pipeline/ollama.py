"""Ollama 호출 공통 헬퍼 — LLM(②)과 VL(③)이 공유.

프로세스 수명 동안 하나의 AsyncClient를 재사용한다 (요청마다 연결 생성 방지).
"""
from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


def shared_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=None, follow_redirects=True)
    return _client


async def chat_json(
    base_url: str,
    model: str,
    prompt: str,
    timeout_seconds: float,
    images_b64: list[str] | None = None,
) -> str:
    """Ollama /api/chat 호출 (JSON 모드, temperature 0). 응답 content 문자열 반환."""
    message: dict = {"role": "user", "content": prompt}
    if images_b64:
        message["images"] = images_b64
    resp = await shared_client().post(
        f"{base_url.rstrip('/')}/api/chat",
        json={
            "model": model,
            "messages": [message],
            "format": "json",
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=timeout_seconds,
    )
    resp.raise_for_status()
    return resp.json()["message"]["content"]

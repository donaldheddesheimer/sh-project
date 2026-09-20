"""Claude Messages API adapter for the episode MCP analyst and reviewer.

The learning layer keeps one small OpenAI-style internal message shape because NIM already uses it. This client
translates that shape to Claude content blocks and translates Claude ``tool_use`` blocks back at the boundary.
"""

from __future__ import annotations

import json

import httpx2


class ClaudeError(RuntimeError):
    pass


class ClaudeClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        workspace_id: str | None = None,
        timeout_s: float = 120.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._api_key = api_key
        self._workspace_id = workspace_id
        self._timeout_s = timeout_s

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, max_tokens: int = 4096) -> dict:
        system, converted = _messages(messages)
        body: dict = {"model": self.model, "max_tokens": max_tokens, "messages": converted}
        if system:
            body["system"] = system
        if tools:
            body["tools"] = [
                {
                    "name": tool["function"]["name"],
                    "description": tool["function"].get("description", ""),
                    "input_schema": tool["function"]["parameters"],
                }
                for tool in tools
            ]
            body["tool_choice"] = {"type": "auto"}
        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        if self._workspace_id:
            headers["anthropic-workspace-id"] = self._workspace_id
        async with httpx2.AsyncClient(timeout=self._timeout_s) as http:
            response = await http.post(f"{self.base_url}/v1/messages", json=body, headers=headers)
        if response.status_code >= 400:
            raise ClaudeError(f"Claude returned {response.status_code}: {response.text[:300]}")
        try:
            blocks = response.json()["content"]
            text = "\n".join(block.get("text", "") for block in blocks if block.get("type") == "text")
            calls = [
                {
                    "id": block["id"],
                    "type": "function",
                    "function": {"name": block["name"], "arguments": json.dumps(block.get("input", {}))},
                }
                for block in blocks
                if block.get("type") == "tool_use"
            ]
            return {"content": text, **({"tool_calls": calls} if calls else {})}
        except (KeyError, TypeError, ValueError) as exc:
            raise ClaudeError(f"unexpected Claude response: {response.text[:300]}") from exc


def _messages(messages: list[dict]) -> tuple[str, list[dict]]:
    """Convert the internal chat/tool history to Claude Messages API content blocks."""
    system: list[str] = []
    converted: list[dict] = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            system.append(str(message.get("content") or ""))
            continue
        if role == "tool":
            block = {
                "type": "tool_result",
                "tool_use_id": message["tool_call_id"],
                "content": str(message.get("content") or ""),
                **({"is_error": True} if message.get("is_error") else {}),
            }
            if converted and converted[-1]["role"] == "user" and _only_tool_results(converted[-1]["content"]):
                converted[-1]["content"].append(block)
            else:
                converted.append({"role": "user", "content": [block]})
            continue
        if role == "assistant":
            blocks: list[dict] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": str(message["content"])})
            for call in message.get("tool_calls") or []:
                raw = call["function"].get("arguments") or "{}"
                arguments = raw if isinstance(raw, dict) else json.loads(raw)
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call["id"],
                        "name": call["function"]["name"],
                        "input": arguments,
                    }
                )
            converted.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            continue
        converted.append({"role": "user", "content": str(message.get("content") or "")})
    return "\n\n".join(system), converted


def _only_tool_results(content: object) -> bool:
    return isinstance(content, list) and bool(content) and all(
        isinstance(block, dict) and block.get("type") == "tool_result" for block in content
    )

"""Analysts: the agent that answers an episode's incidents by testing plans in branches and picking one.

``MockAnalyst`` drives the ScenarioService pipeline with the rule-based mock (no model, no key).
``ModelAnalyst`` is an MCP client of this app's scenario tools (in-process by default, or MCP_URL over HTTP).
It lists the tools, hands them to Claude or Nemotron and runs each tool call the model makes. Either analyst
returns the id of the analysis it completed. When AGENT_MAY_IMPLEMENT is on, the analyst applies its
recommendation through the implementor (the mock directly, a model with ``implement_recommendation``); the
episode service implements it if the model forgets.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from mcp import Client
from mcp.server.mcpserver import MCPServer

from app.agent.chat import ChatClient
from app.learning.implementor import Implementor
from app.models.episode import MemoryMode
from app.models.scenario import ScenarioStatus
from app.services.scenarios import ScenarioService

OnRun = Callable[[str], None]  # told the analysis run id as soon as it exists

MAX_STEPS = 16  # model turns per analysis
TOOL_READ_TIMEOUT_S = 180.0  # simulate_plans blocks while its branches run
TOOLS = {
    "start_analysis",
    "validate_plan",
    "simulate_plans",
    "get_analysis",
    "submit_recommendation",
    "implement_recommendation",
    "recall_experience",
}


class AnalystError(RuntimeError):
    pass


class MockAnalyst:
    name = "mock"

    def __init__(self, scenarios: ScenarioService, implementor: Implementor, may_implement: bool):
        self._scenarios = scenarios
        self._implementor = implementor
        self._may_implement = may_implement

    async def run(self, incident_ids: list[str], on_run: OnRun, memory_mode: MemoryMode = "use") -> str:
        a = await self._scenarios.open(None, None, True, self.name, memory_mode=memory_mode, incident_ids=incident_ids)
        on_run(a.run.id)
        await self._scenarios.run_pipeline(a)  # a cancel (superseded) propagates; failures fail the run
        if a.run.status is not ScenarioStatus.COMPLETED:
            raise AnalystError(a.run.error or f"{a.run.id} ended {a.run.status.value}")
        if self._may_implement:
            await self._implementor.implement(a.run.id, by="agent")
        return a.run.id


EPISODE_NOTE = """

You are running autonomously: nobody answers questions. Work only through the tools, and finish
within {steps} turns: start_analysis, simulate plans (validate first if unsure), submit_recommendation{implement}.
Lessons in `experience` come from earlier episodes; use them to choose what to simulate first, never
instead of simulating."""


class ModelAnalyst:
    def __init__(
        self, name: str, chat: ChatClient, server: MCPServer | str, timeout_s: float, may_implement: bool
    ):
        self.name = name
        self._chat = chat
        self._server = server
        self._timeout_s = timeout_s
        self._may_implement = may_implement

    async def run(self, incident_ids: list[str], on_run: OnRun, memory_mode: MemoryMode = "use") -> str:
        async with asyncio.timeout(self._timeout_s):
            return await self._loop(incident_ids, on_run, memory_mode)

    async def _loop(self, incident_ids: list[str], on_run: OnRun, memory_mode: MemoryMode) -> str:
        async with Client(self._server, read_timeout_seconds=TOOL_READ_TIMEOUT_S) as client:
            listed = await client.list_tools()
            tools = [
                {
                    "type": "function",
                    "function": {"name": t.name, "description": t.description or "", "parameters": t.input_schema},
                }
                for t in listed.tools
                if t.name in TOOLS and (self._may_implement or t.name != "implement_recommendation")
            ]
            implement = ", then implement_recommendation" if self._may_implement else ""
            messages: list[dict] = [
                {"role": "system", "content": (client.instructions or "") + EPISODE_NOTE.format(steps=MAX_STEPS, implement=implement)},
                {"role": "user", "content": f"Active incidents: {', '.join(incident_ids)}. Respond now."},
            ]
            run_id: str | None = None
            submitted = implemented = False
            for _ in range(MAX_STEPS):
                message = await self._chat.chat(messages, tools)
                if not isinstance(message, dict):
                    raise AnalystError(f"{self.name} returned an invalid chat response")
                content = message.get("content")
                reply_errors: list[str] = []
                if not isinstance(content, str):
                    if content is not None:
                        reply_errors.append("assistant content was not text")
                    content = ""
                calls, call_errors = self._tool_calls(message.get("tool_calls"))
                reply_errors.extend(call_errors)
                # Keep the history small and syntactically valid for the next model turn. A malformed tool call
                # must not be replayed to the model provider, where it can make an otherwise recoverable reply fail.
                messages.append({"role": "assistant", "content": content, **({"tool_calls": calls} if calls else {})})
                if not calls:
                    if reply_errors:
                        messages.append({"role": "user", "content": self._retry_message(reply_errors)})
                        continue
                    if submitted:
                        break  # done; the episode service implements if the model did not
                    messages.append({"role": "user", "content": "Continue with the tools until you have called submit_recommendation."})
                    continue
                for call in calls:
                    name = call["function"]["name"]
                    text, ok = await self._call(
                        client, name, call["function"].get("arguments"), incident_ids, memory_mode
                    )
                    if ok and name == "start_analysis":
                        run_id = _field(text, "run_id")
                        if run_id:
                            on_run(run_id)
                    submitted |= ok and name == "submit_recommendation"
                    implemented |= ok and name == "implement_recommendation"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            # Tool results are JSON. Do not slice them to an arbitrary character limit: a partial
                            # object makes the next model turn see invalid JSON, especially for start_analysis on
                            # a larger network. MCP payloads are deliberately compact at their source instead.
                            "content": text,
                            "is_error": not ok,
                        }
                    )
                if reply_errors:
                    messages.append({"role": "user", "content": self._retry_message(reply_errors)})
                if submitted and (implemented or not self._may_implement):
                    break
            if run_id is None or not submitted:
                raise AnalystError(f"{self.name} did not submit a recommendation within {MAX_STEPS} turns")
            return run_id

    def _tool_calls(self, raw_calls: object) -> tuple[list[dict], list[str]]:
        """Keep only provider-safe calls, so one malformed model reply remains recoverable."""
        if raw_calls is None:
            return [], []
        if not isinstance(raw_calls, list):
            return [], ["tool_calls was not a list"]
        allowed = TOOLS if self._may_implement else TOOLS - {"implement_recommendation"}
        calls: list[dict] = []
        errors: list[str] = []
        call_ids: set[str] = set()
        for index, call in enumerate(raw_calls, start=1):
            if not isinstance(call, dict):
                errors.append(f"tool call {index} was not an object")
                continue
            call_id = call.get("id")
            if not isinstance(call_id, str) or not call_id:
                errors.append(f"tool call {index} had no string id")
                continue
            if call_id in call_ids:
                errors.append(f"tool call {index} repeated id {call_id!r}")
                continue
            function = call.get("function")
            if not isinstance(function, dict):
                errors.append(f"tool call {index} had no function object")
                continue
            name = function.get("name")
            if not isinstance(name, str) or name not in allowed:
                errors.append(f"tool call {index} named an unavailable tool")
                continue
            raw_arguments = function.get("arguments")
            if raw_arguments is None:
                arguments = "{}"
            elif isinstance(raw_arguments, str):
                arguments = raw_arguments
            else:
                try:
                    arguments = json.dumps(raw_arguments, separators=(",", ":"))
                except (TypeError, ValueError):
                    errors.append(f"tool call {index} had unserializable arguments")
                    continue
            call_ids.add(call_id)
            calls.append({"id": call_id, "type": "function", "function": {"name": name, "arguments": arguments}})
        return calls, errors

    @staticmethod
    def _retry_message(errors: list[str]) -> str:
        detail = "; ".join(errors[:4])
        return (
            f"Some requested tool calls were not run: {detail}. "
            "Call one of the listed tools with a string id, function name and JSON-object arguments."
        )

    async def _call(
        self, client: Client, name: str, raw_args: object, incident_ids: list[str], memory_mode: MemoryMode
    ) -> tuple[str, bool]:
        """Run one tool call; returns the result text and whether it succeeded (tool errors go back to the model)."""
        if not isinstance(name, str) or name not in TOOLS:
            return f"unknown tool {name}", False
        if isinstance(raw_args, dict):
            args = raw_args
        elif raw_args is None:
            args = {}
        elif not isinstance(raw_args, str):
            return "arguments are not valid JSON: expected an object or JSON string", False
        else:
            try:
                args = json.loads(raw_args)
            except (json.JSONDecodeError, TypeError) as exc:
                return f"arguments are not valid JSON: {exc}", False
        if not isinstance(args, dict):
            return f"unknown tool {name} or arguments that are not an object", False
        if name == "start_analysis":  # the episode decides which incidents are solved together
            args.update(incident_ids=incident_ids, agent=self.name, memory_mode=memory_mode)
        try:
            result = await client.call_tool(name, args)
            text = "\n".join(text for c in result.content if isinstance(text := getattr(c, "text", None), str))
            if not text:
                raise ValueError("tool returned no text content")
            return text, not result.is_error
        except asyncio.CancelledError:
            raise  # a superseded or stopped episode must still cancel its outstanding MCP request
        except Exception as exc:
            detail = " ".join(str(exc).split())[:500] or type(exc).__name__
            return f"{name} failed before returning a result: {type(exc).__name__}: {detail}", False


def _field(text: str, key: str) -> str | None:
    try:
        value = json.loads(text).get(key)
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None

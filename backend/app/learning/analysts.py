"""Analysts: the agent that answers an episode's incidents by testing plans in branches and picking one.

``MockAnalyst`` drives the ScenarioService pipeline with the rule-based mock (no model, no key).
``NemotronAnalyst`` is an MCP client of this app's scenario tools (in-process by default, or MCP_URL over HTTP):
NIM does not speak MCP, so the loop lists the tools, hands them to the OpenAI-compatible NIM endpoint and runs
each tool call the model makes. Either returns the id of the analysis it completed. When AGENT_MAY_IMPLEMENT is on
the analyst applies its recommendation through the implementor (the mock directly, Nemotron with the
``implement_recommendation`` tool); the episode service implements it if Nemotron forgets.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable

from mcp import Client
from mcp.server.mcpserver import MCPServer

from app.agent.nemotron import NimClient
from app.learning.implementor import Implementor
from app.models.episode import MemoryMode
from app.models.scenario import ScenarioStatus
from app.services.scenarios import ScenarioService

OnRun = Callable[[str], None]  # told the analysis run id as soon as it exists

MAX_STEPS = 16  # model turns per analysis
MAX_TOOL_RESULT_CHARS = 16000
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


class NemotronAnalyst:
    name = "nemotron"

    def __init__(self, nim: NimClient, server: MCPServer | str, timeout_s: float, may_implement: bool):
        self._nim = nim
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
                message = await self._nim.chat(messages, tools)
                calls = message.get("tool_calls") or []
                # keep the history small: no reasoning text, just what the model said and called
                messages.append({"role": "assistant", "content": message.get("content") or "", **({"tool_calls": calls} if calls else {})})
                if not calls:
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
                    messages.append({"role": "tool", "tool_call_id": call["id"], "content": text[:MAX_TOOL_RESULT_CHARS]})
                if submitted and (implemented or not self._may_implement):
                    break
            if run_id is None or not submitted:
                raise AnalystError(f"Nemotron did not submit a recommendation within {MAX_STEPS} turns")
            return run_id

    async def _call(
        self, client: Client, name: str, raw_args: str | None, incident_ids: list[str], memory_mode: MemoryMode
    ) -> tuple[str, bool]:
        """Run one tool call; returns the result text and whether it succeeded (tool errors go back to the model)."""
        try:
            args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
        except json.JSONDecodeError as exc:
            return f"arguments are not valid JSON: {exc}", False
        if name not in TOOLS or not isinstance(args, dict):
            return f"unknown tool {name} or arguments that are not an object", False
        if name == "start_analysis":  # the episode decides which incidents are solved together
            args.update(incident_ids=incident_ids, agent=self.name, memory_mode=memory_mode)
        result = await client.call_tool(name, args)
        text = "\n".join(getattr(c, "text", "") for c in result.content)
        return text, not result.is_error


def _field(text: str, key: str) -> str | None:
    try:
        value = json.loads(text).get(key)
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None

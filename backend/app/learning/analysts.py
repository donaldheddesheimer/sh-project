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
from pydantic import ValidationError

from app.agent.base import CandidatePlan
from app.agent.chat import ChatClient
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
within {steps} turns. The next valid tool is exposed at each stage: call start_analysis exactly once,
then simulate_plans with your candidates (it validates them automatically), then submit_recommendation{implement}.
Do not repeat a completed stage. Lessons in `experience` come from earlier episodes; use them to choose
what to simulate first, never instead of simulating.
For simulate_plans, use exactly: {{"run_id":"SCN-...","plans":[{{"id":"short-id","name":"Short name",
"description":"What changes","policies":[],"corridor":null,"reroutes":[]}}]}}."""


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
        try:
            async with asyncio.timeout(self._timeout_s):
                return await self._loop(incident_ids, on_run, memory_mode)
        except ExceptionGroup as exc:
            # The MCP client's AnyIO task group wraps chat/provider errors during context
            # shutdown. Surface the useful leaf in the episode instead of only "ExceptionGroup".
            leaf: BaseException = exc
            while isinstance(leaf, BaseExceptionGroup) and leaf.exceptions:
                leaf = leaf.exceptions[0]
            raise AnalystError(f"{type(leaf).__name__}: {leaf}") from exc

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
            tools_by_name = {tool["function"]["name"]: tool for tool in tools}
            implement = ", then implement_recommendation" if self._may_implement else ""
            messages: list[dict] = [
                {"role": "system", "content": (client.instructions or "") + EPISODE_NOTE.format(steps=MAX_STEPS, implement=implement)},
                {"role": "user", "content": f"Active incidents: {', '.join(incident_ids)}. Respond now."},
            ]
            run_id: str | None = None
            simulated = submitted = implemented = False
            for _ in range(MAX_STEPS):
                if submitted and (implemented or not self._may_implement):
                    break
                if run_id is None:
                    allowed = ["start_analysis"]
                elif not simulated:
                    allowed = ["simulate_plans"]
                elif not submitted:
                    allowed = ["submit_recommendation"]
                else:
                    allowed = ["implement_recommendation"]
                active_tools = [tools_by_name[name] for name in allowed if name in tools_by_name]
                message = await self._chat.chat(messages, active_tools)
                calls = message.get("tool_calls") or []
                # keep the history small: no reasoning text, just what the model said and called
                messages.append({"role": "assistant", "content": message.get("content") or "", **({"tool_calls": calls} if calls else {})})
                if not calls:
                    messages.append({"role": "user", "content": f"Continue now by calling {allowed[0]}."})
                    continue
                for call in calls:
                    name = call["function"]["name"]
                    if name not in allowed:
                        text, ok = f"{name} is not available now; call {allowed[0]}", False
                    else:
                        text, ok = await self._call(
                            client, name, call["function"].get("arguments"), incident_ids, memory_mode
                        )
                    if ok and name == "start_analysis":
                        run_id = _field(text, "run_id")
                        if run_id:
                            on_run(run_id)
                    simulated |= ok and name == "simulate_plans"
                    submitted |= ok and name == "submit_recommendation"
                    implemented |= ok and name == "implement_recommendation"
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call["id"],
                            "content": text[:MAX_TOOL_RESULT_CHARS],
                            "is_error": not ok,
                        }
                    )
            if run_id is None or not submitted:
                raise AnalystError(f"{self.name} did not submit a recommendation within {MAX_STEPS} turns")
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
        if name == "simulate_plans":
            args, error = _normalise_simulation_args(args)
            if error:
                return error, False
        if name == "start_analysis":  # the episode decides which incidents are solved together
            args.update(incident_ids=incident_ids, agent=self.name, memory_mode=memory_mode)
        result = await client.call_tool(name, args)
        text = "\n".join(getattr(c, "text", "") for c in result.content)
        return text, not result.is_error


def _normalise_simulation_args(args: dict) -> tuple[dict, str | None]:
    """Accept common model encodings, then give field-level errors before MCP validation."""
    raw_plans = args.get("plans")
    if isinstance(raw_plans, str):
        try:
            raw_plans = json.loads(raw_plans)
        except json.JSONDecodeError as exc:
            return args, f"plans must be a JSON array, not an invalid JSON string: {exc}"
    if isinstance(raw_plans, dict):
        raw_plans = [raw_plans]
    if not isinstance(raw_plans, list) or not raw_plans:
        return args, "plans must be a non-empty array of CandidatePlan objects"

    plans: list[dict] = []
    for index, raw_plan in enumerate(raw_plans):
        if isinstance(raw_plan, str):
            try:
                raw_plan = json.loads(raw_plan)
            except json.JSONDecodeError as exc:
                return args, f"plans[{index}] is not valid JSON: {exc}"
        if isinstance(raw_plan, dict):
            for field in ("policies", "reroutes"):
                if isinstance(raw_plan.get(field), dict):
                    raw_plan[field] = [raw_plan[field]]
        try:
            plan = CandidatePlan.model_validate(raw_plan)
        except ValidationError as exc:
            details = "; ".join(
                f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
                for error in exc.errors(include_input=False)[:6]
            )
            return args, f"plans[{index}] is invalid: {details}"
        plans.append(plan.model_dump(mode="json"))
    return {**args, "plans": plans}, None


def _field(text: str, key: str) -> str | None:
    try:
        value = json.loads(text).get(key)
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, str) else None

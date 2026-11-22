"""Shared setup for every Band agent in this repo: one LLM factory, one runner."""
import asyncio
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
logging.basicConfig(level=logging.INFO)


def load_shop() -> dict:
    return json.loads((ROOT / "data" / "shop.json").read_text(encoding="utf-8"))


def _trim(messages, budget_chars: int):
    """Keep every system message plus the newest messages that fit in budget_chars.

    Band's checkpointer keeps the whole room history and every tool call. The full record stays in
    the Band room, so the model only needs the recent slice. Never start the slice on a tool result
    whose tool call was cut off.
    """
    system = [m for m in messages if m.type == "system"]
    rest = [m for m in messages if m.type != "system"]
    kept, used = [], 0
    for m in reversed(rest):
        size = len(str(m.content)) + len(str(getattr(m, "tool_calls", "") or ""))
        if kept and used + size > budget_chars:
            break
        kept.append(m)
        used += size
    kept.reverse()
    while kept and kept[0].type == "tool":
        kept.pop(0)
    return system + kept


def make_llm(config_name: str = ""):
    """Any OpenAI-compatible endpoint (Groq free tier by default), from .env.

    Each agent can pick its own model with <NAME>_MODEL (e.g. CRITIC_MODEL). On Groq's free tier
    the rate limit is per model, so giving agents different models keeps them from starving each other.
    Free tiers also cap tokens per request/minute (Groq: 8,000), so history is trimmed to HISTORY_CHARS.
    """
    from langchain_openai import ChatOpenAI

    class TrimmedChatOpenAI(ChatOpenAI):
        history_chars: int = 9000

        def _generate(self, messages, *a, **k):
            return super()._generate(_trim(messages, self.history_chars), *a, **k)

        async def _agenerate(self, messages, *a, **k):
            return await super()._agenerate(_trim(messages, self.history_chars), *a, **k)

        def _stream(self, messages, *a, **k):
            yield from super()._stream(_trim(messages, self.history_chars), *a, **k)

        async def _astream(self, messages, *a, **k):
            async for chunk in super()._astream(_trim(messages, self.history_chars), *a, **k):
                yield chunk

    load_dotenv(ROOT / ".env")
    model = os.environ.get(f"{config_name.upper()}_MODEL") or os.environ.get("LLM_MODEL", "openai/gpt-oss-120b")
    logging.getLogger(config_name or "llm").info("model: %s", model)
    return TrimmedChatOpenAI(
        history_chars=int(os.environ.get("HISTORY_CHARS", "9000")),
        model=model,
        base_url=os.environ.get("LLM_BASE_URL", "https://api.groq.com/openai/v1"),
        api_key=os.environ["LLM_API_KEY"],
        temperature=0.2,
        max_retries=10,
    )


def run_band_agent(config_name: str, instructions: str) -> None:
    """Wrap a LangGraph agent for Band and listen on its WebSocket forever."""
    from band import Agent
    from band.adapters import LangGraphAdapter, LangGraphAdapterConfig
    from band.config import load_agent_config
    from langgraph.checkpoint.memory import InMemorySaver

    async def main() -> None:
        adapter = LangGraphAdapter(
            LangGraphAdapterConfig(custom_section=instructions, recursion_limit=12),
            llm=make_llm(config_name),
            checkpointer=InMemorySaver(),
        )
        os.chdir(ROOT)  # load_agent_config reads agent_config.yaml from the cwd
        agent_id, api_key = load_agent_config(config_name)
        agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)
        logging.getLogger(config_name).info("%s is live on Band. Ctrl+C to stop.", config_name)
        await agent.run()

    asyncio.run(main())

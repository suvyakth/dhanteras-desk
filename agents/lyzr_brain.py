"""Band agents whose brains are Lyzr agents.

Band owns the room: identity, @mention routing, mention-scoped visibility, the human in the loop.
Lyzr owns the thinking: each Band agent forwards what it is shown to its own Lyzr agent, which keeps
one memory session per Band room, and posts the answer back with the @mentions the Lyzr agent chose.
"""
import asyncio
import json
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

from band.core.simple_adapter import SimpleAdapter

ROOT = Path(__file__).resolve().parent.parent
LYZR_BASE = "https://agent-prod.studio.lyzr.ai/v3"
AGENTS_FILE = ROOT / "lyzr_agents.json"  # agent IDs (not secrets), written by create_lyzr_agents.py
log = logging.getLogger("lyzr")

# Every Lyzr agent answers in this shape so the Band side knows whom to @mention.
REPLY_FORMAT = """
REPLY FORMAT (always):
First line: TO: <comma-separated names to @mention>   or   TO: none   (to stay silent)
Then a blank line, then your message. No other preamble."""

_TO_LINE = re.compile(r"^\s*TO:\s*(.*)$", re.IGNORECASE)


def parse_reply(text: str) -> tuple[list[str] | None, str]:
    """Split a Lyzr answer into (@mention names, message body).

    Returns None for the names when there is no TO: line (caller replies to the sender);
    an explicit "TO: none" returns [] (stay silent).
    """
    lines = text.strip().splitlines()
    if lines and (m := _TO_LINE.match(lines[0])):
        names = [n.strip().lstrip("@") for n in m.group(1).split(",")]
        names = [n for n in names if n and n.lower() != "none"]
        return names, "\n".join(lines[1:]).strip()
    return None, text.strip()


def _api_key() -> str:
    load_dotenv(ROOT / ".env")
    return os.environ["LYZR_API_KEY"]


def lyzr_agent_id(name: str) -> str:
    return json.loads(AGENTS_FILE.read_text(encoding="utf-8"))[name]


async def ask_lyzr(agent_id: str, message: str, session_id: str) -> str:
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            f"{LYZR_BASE}/inference/chat/",
            headers={"x-api-key": _api_key(), "Content-Type": "application/json"},
            json={"user_id": "dhanteras-desk", "agent_id": agent_id,
                  "session_id": session_id, "message": message},
        )
        r.raise_for_status()
        data = r.json()
    # Be tolerant about the response field name.
    for key in ("response", "message", "output", "content"):
        if isinstance(data.get(key), str):
            return data[key]
    return json.dumps(data)


STARTED_AT = time.time()
RUN_TAG = str(int(STARTED_AT))  # fresh Lyzr memory on every restart

# Code-enforced routing: who each role may hand off to, whatever its LLM says.
FIXED_ROUTES = {"Scout": ["Planner", "Critic"], "Critic": ["Planner"]}


MAX_NEWS_AGE_DAYS = 120  # this festive season only


async def search_news(query: str, n: int = 3) -> list[dict]:
    """Real headlines from Google News RSS (public feed, no key), newest first, this season only."""
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.get("https://news.google.com/rss/search",
                             params={"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
        r.raise_for_status()
    now = datetime.now(timezone.utc)
    items = []
    for it in ET.fromstring(r.text).iter("item"):
        try:
            published = parsedate_to_datetime(it.findtext("pubDate") or "")
        except (TypeError, ValueError):
            continue
        if (now - published).days <= MAX_NEWS_AGE_DAYS:
            items.append((published, {"title": it.findtext("title") or "",
                                      "date": published.strftime("%d %b %Y"), "url": it.findtext("link") or ""}))
    items.sort(key=lambda p: p[0], reverse=True)
    return [i for _, i in items[:n]]


CREW = ("Planner", "Critic", "Scout")


def canonical_handle(mention: str) -> str:
    """Band mention names are case-sensitive: map 'critic', '@Critic', 'ozardravindra/critic' to 'Critic',
    and any spelling of the owner to their exact handle."""
    tail = re.sub(r"[^a-z0-9]", "", mention.strip().lstrip("@").split("/")[-1].lower())
    for name in CREW:
        if tail == name.lower():
            return name
    owner = load_shop()["owner_handle"]
    if tail == re.sub(r"[^a-z0-9]", "", owner.lower()):
        return owner
    return mention.strip().lstrip("@")


FESTIVE_WEEKS = 3


def load_shop() -> dict:
    return json.loads((ROOT / "data" / "shop.json").read_text(encoding="utf-8"))


def shop_search_terms() -> list[str]:
    return [i["name"].split(",")[0] for i in load_shop()["items"]]


def baseline_table() -> str:
    """Sales-based starting point, computed in code so the LLMs never do the arithmetic."""
    shop = load_shop()
    rows, total = [], 0
    for i in shop["items"]:
        units = max(0, i["weekly_sales"] * FESTIVE_WEEKS - i["stock"])
        total += units * i["cost_inr"]
        rows.append(f"{i['sku']}: {units} units (weekly_sales {i['weekly_sales']} x {FESTIVE_WEEKS} - stock "
                    f"{i['stock']}), cost_inr {i['cost_inr']}, margin {i['price_inr'] - i['cost_inr']}/unit")
    return ("BASELINE (computed by code):\n" + "\n".join(rows) +
            f"\nBaseline total {total} vs budget_inr {shop['budget_inr']}")


_PLAN_LINE = re.compile(r"([A-Z]+(?:-[A-Z0-9]+)+)\W+(\d+)\s*units", re.IGNORECASE)


def check_plan(text: str) -> list[str]:
    """Code-side audit of a plan: known SKUs, every SKU present, total within budget."""
    shop = load_shop()
    items = {i["sku"]: i for i in shop["items"]}
    found = {sku.upper(): int(n) for sku, n in _PLAN_LINE.findall(text)}
    problems = [f"unknown SKU {s}" for s in found if s not in items]
    problems += [f"{s} missing from plan" for s in items if s not in found]
    total = sum(n * items[s]["cost_inr"] for s, n in found.items() if s in items)
    budget = shop["budget_inr"]
    if total > budget:
        problems.append(f"total {total} exceeds budget_inr {budget}")
    cut = []
    for s, n in found.items():
        if s not in items:
            continue
        base = max(0, items[s]["weekly_sales"] * FESTIVE_WEEKS - items[s]["stock"])
        if base and n < base / 2:
            problems.append(f"{s} cut to {n}, more than half below baseline {base}: too drastic")
        if n < base:
            cut.append(s)
    if cut and total < 0.9 * budget:
        problems.append(f"items cut below baseline ({', '.join(cut)}) while {budget - total} of the budget "
                        "is unused: cut only as much as needed")
    return problems, total


class LyzrAdapter(SimpleAdapter):
    """Forward each message this Band agent is @mentioned in to its Lyzr agent; post the answer."""

    def __init__(self, name: str):
        super().__init__()
        self.name = name
        self.lyzr_id = lyzr_agent_id(name)

    async def build_prompt(self, prompt: str, tools) -> tuple[str, dict]:
        """Hook for roles that gather evidence first. Returns (prompt, evidence by id).

        Default: replace markdown links with their label, so agents cite E-numbers, not long URLs.
        """
        return re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", prompt), {}

    def postprocess(self, body: str, evidence: dict) -> str:
        return body

    def route(self, mentions, msg):
        """Hook for code-enforced routing rules. Default: keep what the LLM chose."""
        return mentions

    @staticmethod
    def is_stale(msg) -> bool:
        created = getattr(msg, "created_at", None)
        return isinstance(created, datetime) and created.timestamp() < STARTED_AT - 30

    async def on_message(self, msg, tools, history, participants_msg, contacts_msg, *,
                         is_session_bootstrap, room_id):
        if self.is_stale(msg):  # a restart must not replay a stale backlog into a fresh run
            await tools.no_reply("message predates this agent's start")
            return
        sender = msg.sender_name or msg.sender_type
        prompt = f"[{sender}] {msg.content}"
        if participants_msg:
            prompt = f"(Room participants update: {participants_msg})\n{prompt}"
        await tools.send_event(f"{self.name} is thinking with its Lyzr agent", "thought")
        try:
            prompt, evidence = await self.build_prompt(prompt, tools)
            answer = await ask_lyzr(self.lyzr_id, prompt, session_id=f"{room_id}:{self.name}:{RUN_TAG}")
        except Exception as e:  # report in the room instead of failing silently
            log.exception("Lyzr call failed")
            await tools.send_event(f"Lyzr call failed: {e}", "error")
            await tools.no_reply("lyzr error")
            return
        mentions, body = parse_reply(answer)
        body = self.postprocess(body, evidence)
        if self.name in FIXED_ROUTES:  # enforced boundary, not a prompt suggestion
            mentions = FIXED_ROUTES[self.name]
        mentions = self.route(mentions, msg)
        if mentions:
            mentions = list(dict.fromkeys(canonical_handle(m) for m in mentions))
        if mentions is not None:  # Band rejects self-mentions
            mentions = [m for m in mentions if not self._is_me(m)] or (None if mentions else [])
        if mentions is None and msg.sender_name and not self._is_me(msg.sender_name):
            mentions = [msg.sender_name]  # no usable TO: line -> answer whoever asked
        log.info("%s -> mentions %s (sender %s)", self.name, mentions, msg.sender_name)
        if not mentions or not body:
            await tools.no_reply("Lyzr agent chose not to reply")
            return
        try:
            await tools.send_message(body, mentions=mentions)
        except Exception as e:
            if "cannot_mention_self" not in str(e) or not msg.sender_name or self._is_me(msg.sender_name):
                raise
            log.warning("%s tried to mention itself via %s; replying to %s", self.name, mentions, msg.sender_name)
            await tools.send_message(body, mentions=[msg.sender_name])

    def _is_me(self, mention: str) -> bool:
        """'Critic', '@Critic', 'ozardravindra/critic', 'Critic.' all count as this agent."""
        tail = mention.strip().lstrip("@").split("/")[-1]
        return re.sub(r"[^a-z0-9]", "", tail.lower()) == self.name.lower()


class ScoutAdapter(LyzrAdapter):
    """Scout searches real news first; its Lyzr agent may only cite those results, by ID.

    Code swaps each [E#] for the real headline and URL and drops any finding line that cites
    nothing it fetched, so a Scout finding can never carry an invented source.
    """

    async def build_prompt(self, prompt, tools):
        if not getattr(self, "_evidence", None):  # search once per run; E-numbers stay stable
            await tools.send_event("Scout is searching live news for festive price and demand evidence", "thought")
            terms = shop_search_terms()
            results = await asyncio.gather(*(search_news(f"Diwali {t} price demand") for t in terms),
                                           return_exceptions=True)
            self._evidence, self._lines = {}, []
            for term, res in zip(terms, results):
                if isinstance(res, Exception):
                    log.warning("search failed for %s: %s", term, res)
                    continue
                for item in res:
                    eid = f"E{len(self._evidence) + 1}"
                    self._evidence[eid] = item
                    self._lines.append(f"[{eid}] ({term}) {item['title']} | {item['date']}")
        block = "\n".join(self._lines) or "(search returned nothing recent)"
        return (f"{prompt}\n\nLIVE SEARCH RESULTS (the only evidence you may use):\n{block}\n\n"
                "Write 2-6 findings, one per line, as: [E#] item | what the headline shows\n"
                "Cite only the [E#] IDs above. Never write a URL yourself. If nothing relevant, say so."), self._evidence

    def postprocess(self, body, evidence):
        kept = []
        for line in body.splitlines():
            ids = [i for i in re.findall(r"\[?(E\d+)\]?", line) if i in evidence]
            if "|" in line and not ids:
                log.warning("Scout dropped an unsourced finding: %s", line)
                continue
            for i in ids:
                e = evidence[i]
                publisher = e["title"].rsplit(" - ", 1)[-1]
                line = re.sub(rf"\[?{i}\]?", "", line, count=1).strip(" -.")
                line = f"**{i}** {line} ([{publisher}, {e['date']}]({e['url']}))"
            line = re.sub(r"(?<!\()https?://\S+", "", line)  # strip any URL Scout wrote itself
            kept.append(line)
        return "\n".join(kept).strip()


class CriticAdapter(LyzrAdapter):
    """Critic silently files Scout's evidence, then checks each plan against it plus the shop data.

    Scout copies Critic on its findings so the evidence trail is visible in the room, but Critic
    never answers findings directly: it only judges plans, so the crew cannot fan out into a loop.
    """

    def __init__(self, name):
        super().__init__(name)
        self._scout_evidence = ""

    async def on_message(self, msg, tools, history, participants_msg, contacts_msg, **kw):
        if (msg.sender_name or "").lower() == "scout" and not self.is_stale(msg):
            self._scout_evidence = msg.content
            await tools.no_reply("evidence filed; Critic judges plans, not findings")
            return
        await super().on_message(msg, tools, history, participants_msg, contacts_msg, **kw)

    async def build_prompt(self, prompt, tools):
        evidence = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", self._scout_evidence)  # drop long URLs
        self._problems, total = check_plan(prompt)
        audit = "; ".join(self._problems) or "all SKUs present, arithmetic and budget OK"
        return (f"{baseline_table()}\n\nSCOUT EVIDENCE:\n{evidence or '(none posted)'}\n\n"
                f"CODE CHECK of this plan (trust it): total {total}; {audit}\n\nPLAN TO CHECK:\n{prompt}"), {}

    def postprocess(self, body, evidence):
        # Enforced veto: Critic's LLM cannot approve a plan that fails the code check.
        if getattr(self, "_problems", None) and body.lstrip().upper().startswith("APPROVED"):
            log.warning("Critic LLM approved a plan that failed the code check: %s", self._problems)
            return "BLOCKED (code check)\n" + "\n".join(f"- {p}" for p in self._problems)
        return body


MAX_VETOES = 2


class PlannerAdapter(LyzrAdapter):
    """Planner can address the owner only after Critic approves, or after MAX_VETOES blocks."""

    def __init__(self, name):
        super().__init__(name)
        self._reset()

    def _reset(self):
        self._vetoes, self._approved, self._has_findings = 0, False, False

    async def on_message(self, msg, tools, history, participants_msg, contacts_msg, **kw):
        sender = (msg.sender_name or "").lower()
        verdict = msg.content.lstrip().upper()
        if self.is_stale(msg):
            pass  # base class drops it; it must not touch run state either
        elif sender == "critic":
            self._approved = "APPROVED" in verdict[:40]
            self._vetoes += "BLOCKED" in verdict[:40]
        elif sender == "scout":
            self._has_findings = True
        elif sender != "planner":  # a human: a decision ends the run, anything else starts one
            if not re.search(r"\b(approve|reject)", msg.content, re.IGNORECASE):
                self._reset()
        self._sender = sender
        await super().on_message(msg, tools, history, participants_msg, contacts_msg, **kw)

    async def build_prompt(self, prompt, tools):
        prompt, _ = await super().build_prompt(prompt, tools)
        status = (f"RUN STATUS: Scout findings {'RECEIVED' if self._has_findings else 'not yet requested'}; "
                  f"Critic {'APPROVED' if self._approved else 'has not approved'}; "
                  f"vetoes {self._vetoes} of {MAX_VETOES}.")
        if self._has_findings and self._sender == "scout":
            status += " NEXT: write the plan from these findings now and address Critic. Do not ask Scout again."
        elif self._vetoes >= MAX_VETOES and not self._approved:
            status += " NEXT: escalate. Send the owner your best within-budget plan and Critic's open objections."
        elif self._sender == "critic" and not self._approved:
            status += " NEXT: revise the plan and send it to Critic."
        elif self._approved:
            status += " NEXT: send the owner the final plan and ask for approve or reject."
        return f"{prompt}\n\n{baseline_table()}\n\n{status}", {}

    def postprocess(self, body, evidence):
        """Stamp every plan with the code-computed total, so nobody has to trust the LLM's maths.

        After Critic approves, the owner gets exactly the plan Critic approved, never an LLM re-write.
        """
        if self._approved and self._sender == "critic" and getattr(self, "_last_plan", None):
            owner = load_shop()["owner_handle"]
            return (f"Critic approved this restock plan:\n\n{self._last_plan}\n\n"
                    f"@{owner}, please reply **approve** or **reject**.")
        if not _PLAN_LINE.search(body):
            return body
        problems, total = check_plan(body)
        budget = load_shop()["budget_inr"]
        status = "OK" if not problems else "; ".join(problems)
        plan = "\n".join(l for l in body.splitlines() if _PLAN_LINE.search(l))
        self._last_plan = f"{plan}\n\n_Code check: total ₹{total:,} of ₹{budget:,} budget. {status}_"
        return f"{body}\n\n_Code check: total ₹{total:,} of ₹{budget:,} budget. {status}_"

    def route(self, mentions, msg):
        owner = load_shop()["owner_handle"].lower()
        if self._approved and self._sender == "critic":
            return [load_shop()["owner_handle"]]  # approved plan always goes to the human
        if self._has_findings and mentions and any(m.lower().endswith("scout") for m in mentions):
            log.warning("Planner tried to re-ask Scout after findings arrived; sending to Critic")
            return ["Critic"]
        to_owner = any(owner in m.lower().replace(" ", "") for m in mentions or [])
        if to_owner and not self._approved and self._vetoes < MAX_VETOES \
                and (msg.sender_name or "").lower() in ("scout", "critic"):
            log.warning("Planner tried to reach the owner before Critic signed off; sending to Critic")
            return ["Critic"]
        return mentions


ADAPTERS = {"Scout": ScoutAdapter, "Critic": CriticAdapter, "Planner": PlannerAdapter}


def run_lyzr_band_agent(config_name: str, name: str) -> None:
    import asyncio

    from band import Agent
    from band.config import load_agent_config

    async def main() -> None:
        adapter = ADAPTERS.get(name, LyzrAdapter)(name)
        os.chdir(ROOT)
        agent_id, api_key = load_agent_config(config_name)
        agent = Agent.create(adapter=adapter, agent_id=agent_id, api_key=api_key)
        log.info("%s is live on Band, thinking with Lyzr agent %s", name, adapter.lyzr_id)
        await agent.run()

    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())

"""Create (once) the Lyzr agents that act as the brains of the Band agents.

Run:  python agents/create_lyzr_agents.py
Writes their IDs to lyzr_agents.json. Re-running skips agents that already exist there.
"""
import json
import sys

import httpx

from common import load_shop
from lyzr_brain import AGENTS_FILE, LYZR_BASE, REPLY_FORMAT, _api_key

shop = load_shop()
SHOP = json.dumps({k: v for k, v in shop.items() if not k.startswith("_")}, separators=(",", ":"))
OWNER = shop["owner_handle"]
MODEL = "gpt-4o-mini"

AGENTS = {
    "Planner": dict(
        role="Restock planner for a small Diwali shop",
        model="gpt-4.1",
        goal="Turn market evidence into a within-budget restock plan the owner can approve",
        instructions=f"""You are Planner in a team chat room with Critic, Scout and the shop owner ({OWNER}).
You know the shop's stock, costs, prices and weekly sales (below). You never browse the web: market facts
come only from Scout's findings, which are numbered E1, E2, ...
- New request from {OWNER}: address Scout ONCE asking for festive price/demand news on our items. Then wait.
- Scout's findings arrive: write the restock plan and address Critic only. Never ask Scout again in the same run.
  Each message you get ends with a BASELINE table computed by code (sales-based units per SKU, total vs budget).
  Plan = one line per SKU, exactly in this form:  SKU: N units - reason
  Start from the baseline units. Change a quantity only where a cited E-number justifies the direction, and
  cut where needed to fit budget_inr (prefer cutting lower-margin or weaker-news items) and give such a line the
  reason "budget cut" (plus any E-number that makes it the right item to cut). Keep changes moderate.
  The total MUST be within budget_inr, also when you escalate to the owner.
  If no finding covers an item and it is unchanged, the reason is "baseline, no news". List all SKUs, even at 0.
  Under 130 words. Cite E-numbers only; never paste URLs. Code checks the arithmetic, so skip cost maths.
- Critic says BLOCKED: fix exactly what it names and send the revised plan to Critic.
- Critic says APPROVED: address {OWNER} with the final plan in 3 lines and ask them to reply approve or reject.
- Critic has blocked twice already: address {OWNER} with your best plan, list Critic's open objections,
  and ask them to approve or reject.
- {OWNER} approves or rejects: acknowledge in one line to {OWNER}.
SHOP DATA: {SHOP}
{REPLY_FORMAT}""",
    ),
    "Critic": dict(
        role="Evidence checker that can veto restock plans",
        model="gpt-4.1",
        goal="Block any plan claim that is not backed by evidence posted in the room",
        instructions="""You are Critic in a team chat room. You only check Planner's plans; you never write plans.
Each plan arrives with the BASELINE table, SCOUT EVIDENCE (findings E1, E2, ...) and a CODE CHECK that has
already verified SKUs, arithmetic and budget. Trust the CODE CHECK; do not redo the maths. Check only:
1. Any quantity that differs from the baseline either cites an E-number whose finding supports that
   direction, or is marked "budget cut" (a cut needed to fit budget_inr is valid without news; only
   question it if a different item would clearly be the better one to cut).
2. Every cited E-number exists in SCOUT EVIDENCE and says what the plan claims.
3. A line at its baseline marked "baseline, no news" needs no evidence.
If the CODE CHECK lists problems, the plan is BLOCKED for those too.
- Anything fails: address Planner, start with BLOCKED, list each failing line and why.
- All pass: address Planner, start with APPROVED and give one sentence why.
Under 80 words. Do not demand evidence that the rules above do not require.
""" + REPLY_FORMAT,
    ),
    "Scout": dict(
        role="Festive market researcher for Indian retail",
        goal="Find current festive price and demand evidence, always with a source URL",
        instructions="""You are Scout in a team chat room. Each request comes with LIVE SEARCH RESULTS: real, recent
news headlines numbered [E1], [E2], ... Return 2-6 findings about festive price or demand for our items,
one per line as:
[E#] item | what the headline shows (one short sentence, no more than it says)
Use only the numbered results; never write a URL or cite anything else. If nothing relevant was found,
say so plainly. Address Planner.
""" + REPLY_FORMAT,
    ),
}


def main() -> None:
    existing = json.loads(AGENTS_FILE.read_text(encoding="utf-8")) if AGENTS_FILE.exists() else {}
    headers = {"x-api-key": _api_key(), "Content-Type": "application/json"}
    update = "--update" in sys.argv
    for name, cfg in AGENTS.items():
        if name in existing and not update:
            print(f"{name}: already exists ({existing[name]}); pass --update to push new instructions")
            continue
        body = {
            "name": f"Dhanteras {name}",
            "description": cfg["role"],
            "agent_role": cfg["role"],
            "agent_goal": cfg["goal"],
            "agent_instructions": cfg["instructions"],
            "provider_id": "OpenAI",
            "model": cfg.get("model", MODEL),
            "llm_credential_id": "lyzr_openai",
            "temperature": 0.2,
            "top_p": 0.9,
            "features": [],
            "tools": [],
        }
        if name in existing:
            r = httpx.put(f"{LYZR_BASE}/agents/{existing[name]}", headers=headers, timeout=60, json=body)
            print(f"{name}: update {'ok' if r.status_code < 400 else f'FAILED {r.status_code} {r.text[:300]}'}")
            continue
        r = httpx.post(f"{LYZR_BASE}/agents/", headers=headers, timeout=60, json=body)
        if r.status_code >= 400:
            print(f"{name}: FAILED {r.status_code} {r.text[:300]}")
            sys.exit(1)
        data = r.json()
        agent_id = data.get("agent_id") or data.get("id") or data.get("_id")
        if not agent_id:
            print(f"{name}: created but no id field in response: {str(data)[:300]}")
            sys.exit(1)
        existing[name] = agent_id
        AGENTS_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")
        print(f"{name}: created {agent_id}")


if __name__ == "__main__":
    main()

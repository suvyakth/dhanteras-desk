"""Offline dry run of one crew run, without Band: real Lyzr brains, real news search, fake room.

Run:  python agents/dry_run.py
Routes each posted message to the agents it @mentions, like the Band room would, and prints the
transcript. Stops when the owner is addressed or after MAX_TURNS.
"""
import asyncio
import json
import sys
from types import SimpleNamespace

import lyzr_brain as lb

MAX_TURNS = 12
OWNER = json.loads((lb.ROOT / "data" / "shop.json").read_text(encoding="utf-8"))["owner_handle"]


class FakeTools:
    def __init__(self, name, outbox):
        self.name, self.outbox = name, outbox

    async def send_message(self, content, mentions=None):
        self.outbox.append((self.name, [m.split("/")[-1].capitalize() for m in mentions or []], content))

    async def send_event(self, content, kind="thought"):
        print(f"   ({self.name} {kind}: {content})")

    async def no_reply(self, reason=""):
        print(f"   ({self.name} stays silent: {reason})")


async def main(request: str) -> None:
    agents = {n: lb.ADAPTERS.get(n, lb.LyzrAdapter)(n) for n in ("Planner", "Critic", "Scout")}
    queue = [(OWNER, ["Planner"], request)]
    for _ in range(MAX_TURNS):
        if not queue:
            break
        sender, mentions, content = queue.pop(0)
        print(f"\n## {sender} -> {', '.join(mentions)}\n{content}")
        if OWNER.lower() in [m.lower() for m in mentions]:
            print("\n== Reached the owner for approve/reject. Run complete.")
            return
        for name in mentions:
            if name in agents:
                outbox = []
                msg = SimpleNamespace(content=content, sender_name=sender, sender_type="agent")
                await agents[name].on_message(msg, FakeTools(name, outbox), [], None, None,
                                              is_session_bootstrap=False, room_id="dry-run")
                queue.extend(outbox)
    print("\n== Stopped without reaching the owner.")


if __name__ == "__main__":
    asyncio.run(main(" ".join(sys.argv[1:]) or "Dhanteras is close. Plan our restock within budget."))

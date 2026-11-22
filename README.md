# Dhanteras Desk

Three agents and one shop owner work one Diwali restock decision together in a [BAND](https://band.ai) room. Each agent thinks with its own [Lyzr](https://studio.lyzr.ai) agent. Code, not prompts, enforces who may talk to whom, which sources count, and that the arithmetic is right. Every run ends with the owner approving or rejecting a plan in the room.

Built for *Diwali Festiverse: The Agent-to-Agent Hackathon (India Edition)*, Research track.

## The crew
| Member | Brain | Its one job | What code enforces |
|---|---|---|---|
| **Scout** | Lyzr, gpt-4o-mini | Searches live news (Google News RSS) for festive price and demand signals on the shop's items | It may cite only headlines it fetched, by number (E1, E2, …). Any line citing nothing it fetched is dropped, and so is any URL it wrote itself. Only news from the last 120 days counts. It always reports to Planner and Critic. |
| **Planner** | Lyzr, gpt-4.1 | Turns the evidence and the shop's stock and sales into a restock plan within budget | It gets a sales-based baseline computed by code. Every plan is stamped with a code-computed total. It asks Scout once per run. It cannot reach the owner until Critic approves, or until Critic has vetoed twice. |
| **Critic** | Lyzr, gpt-4.1 | Judges each plan line against Scout's evidence; replies `BLOCKED` or `APPROVED` | It files Scout's findings silently and judges plans, never findings. It cannot approve a plan that fails the code check: over budget, missing SKUs, cuts too deep, or budget left unused while items are cut. |
| **Owner** (human) | — | Approves or rejects the final plan | After Critic approves, the owner gets exactly the plan Critic approved, never a rewrite. |

## One run
```
owner:   @Planner Dhanteras is close. Plan our restock within budget.
Planner: @Scout  asks for festive price/demand news on our items
Scout:   @Planner @Critic  E1..E9 findings, each linked to a real, dated article
Planner: @Critic  KAJU-KATLI-500: 74 units - budget cut (E4) ...  "Code check: total ₹57,180 of ₹60,000. OK"
Critic:  @Planner BLOCKED / APPROVED with reasons     (at most two vetoes, then escalate)
Planner: @owner   "Critic approved this restock plan: ... reply approve or reject"
```

**What breaks without Band:** each agent sees only the messages that @mention it. Critic judges only what Planner sends it, against evidence Scout copied it on. The owner's reply is the last step of every run. Band's identities, mention routing and mention-scoped visibility are how the crew coordinates. Take the room away and there is nothing left to coordinate.

## Code map
| File | What it does |
|---|---|
| `agents/lyzr_brain.py` | A Band `SimpleAdapter` per agent that forwards to its Lyzr agent, plus the enforced rules: routing, evidence IDs, baseline, plan check, veto limit, stale-backlog guard |
| `agents/create_lyzr_agents.py` | Creates the three Lyzr agents, or updates them with `--update`. Their IDs go in `lyzr_agents.json`. |
| `agents/run_lyzr.py` | `python agents/run_lyzr.py planner\|critic\|scout` runs one Band agent |
| `agents/dry_run.py` | A full run offline: real Lyzr brains and real news, with a simulated room |
| `agents/planner.py`, `agents/critic.py`, `agents/common.py` | The earlier version, using Band's LangGraph adapter and a free Groq model (see PROBLEMS_LOG.md for why it moved to Lyzr) |
| `data/shop.json` | The shop: made up, with five SKUs and a ₹60,000 budget |

## Run it
```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env                              # LYZR_API_KEY
copy agent_config.example.yaml agent_config.yaml    # Band agent IDs + keys (app.band.ai > Agents > Connect remote agent)
python agents/create_lyzr_agents.py                 # once
python agents/run_lyzr.py planner                   # and critic, scout, each in its own terminal
```
In app.band.ai, create a room, add Planner, Critic and Scout, and send `@Planner Dhanteras is close. Plan our restock within budget.` To try it without Band, run `python agents/dry_run.py`.

## Sponsor use
| Sponsor | What we use | Where | What breaks without it |
|---|---|---|---|
| BAND | Agent identities, rooms, @mention routing, mention-scoped visibility, thought and error events, message delivery states | `agents/lyzr_brain.py`, `agents/run_lyzr.py` | All coordination and the human decision step |
| Lyzr | Three Lyzr Studio agents (created and updated through the API), with per-room memory sessions through the inference API | `agents/create_lyzr_agents.py`, `ask_lyzr` | Every agent's reasoning |
| Agent37 | Hosting *(planned)* | — | — |
| Finch | One agent skill *(planned)* | — | — |

What went wrong while building it, and how each problem was fixed, is in [PROBLEMS_LOG.md](PROBLEMS_LOG.md).

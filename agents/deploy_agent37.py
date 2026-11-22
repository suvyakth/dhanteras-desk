"""Deploy the crew to an Agent37 sandbox so it runs 24/7 without a laptop.

Run:  python agents/deploy_agent37.py            create (or reuse) the instance, install, start
      python agents/deploy_agent37.py status     show the agents' processes and log tails
      python agents/deploy_agent37.py restart    pull the latest code and restart the agents

Needs AGENT37_API_KEY and LYZR_API_KEY in .env, and the Band IDs and keys in agent_config.yaml.
The secrets go into the instance's environment variables. They are never written into the repo.
"""
import json
import os
import sys
import time
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
KEY = os.environ["AGENT37_API_KEY"]
API = "https://api.agent37.com/v1/instances"
SANDBOX = "https://sandbox.agent37.app/v1/instances"
NAME = "dhanteras-desk"
REPO = "https://github.com/suvyakth/dhanteras-desk.git"
STATE = ROOT / ".agent37_instance"  # instance id (gitignored)
APP = "$HOME/dhanteras-desk"

START = f"""cd {APP} && for a in planner critic scout; do
  pkill -f "run_lyzr.py $a" 2>/dev/null
  PYTHONIOENCODING=utf-8 nohup .venv/bin/python -u agents/run_lyzr.py $a > $a.log 2>&1 &
done; sleep 1; echo started"""


def env_vars() -> dict:
    cfg = yaml.safe_load((ROOT / "agent_config.yaml").read_text(encoding="utf-8"))
    env = {"LYZR_API_KEY": os.environ["LYZR_API_KEY"]}
    for name, block in cfg.items():
        env[f"{name.upper()}_AGENT_ID"] = block["agent_id"]
        env[f"{name.upper()}_API_KEY"] = block["api_key"]
    return env


def exec_(instance_id: str, command: str, timeout: int = 600) -> dict:
    r = httpx.post(f"{SANDBOX}/{instance_id}/exec", headers={"X-Agent37-Key": KEY},
                   json={"command": command}, timeout=timeout)
    r.raise_for_status()
    out = r.json()
    print(f"$ {command.splitlines()[0][:80]}\n  exit {out.get('exit_code')}\n{(out.get('stdout') or '')[-1500:]}"
          f"{(out.get('stderr') or '')[-800:]}")
    return out


def create() -> str:
    if STATE.exists():
        return STATE.read_text().strip()
    body = {"template": "agent37-base", "name": NAME, "resources": {"cpu": 1, "memory": 1, "disk": 4},
            "auto_sleep": False, "env": env_vars(), "metadata": {"project": NAME}}
    r = httpx.post(API, headers={"Authorization": f"Bearer {KEY}"}, json=body, timeout=300)
    if r.status_code >= 400:
        sys.exit(f"create failed {r.status_code}: {r.text[:500]}")
    inst = r.json()
    STATE.write_text(inst["id"])
    print("created", inst["id"], inst.get("status"), inst.get("url"))
    return inst["id"]


def install(instance_id: str) -> None:
    exec_(instance_id, f"[ -d {APP} ] && (cd {APP} && git pull -q) || git clone -q {REPO} {APP}; echo ok")
    exec_(instance_id, f"cd {APP} && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt"
                       " && echo installed", timeout=900)
    hook = f"{START}\n"
    exec_(instance_id, "mkdir -p ~/.agent37/hooks && cat > ~/.agent37/hooks/post-restart.sh <<'EOF'\n"
                       f"#!/bin/sh\n{hook}EOF\nchmod +x ~/.agent37/hooks/post-restart.sh && echo hook-set")


def status(instance_id: str) -> None:
    exec_(instance_id, f"pgrep -af run_lyzr.py; cd {APP} && for a in planner critic scout; do "
                       "echo \"== $a\"; grep -E 'live on Band|-> mentions|Traceback|Error' $a.log | tail -3; done")


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "deploy"
    if cmd == "status":
        return status(STATE.read_text().strip())
    instance_id = create()
    if cmd == "restart":
        exec_(instance_id, f"cd {APP} && git pull -q && echo pulled")
    else:
        install(instance_id)
    exec_(instance_id, START)
    time.sleep(15)
    status(instance_id)


if __name__ == "__main__":
    main()

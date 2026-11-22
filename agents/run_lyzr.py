"""Run one Band agent with a Lyzr brain.

Run:  python agents/run_lyzr.py planner     (or critic, scout)
The name must have a block in agent_config.yaml (Band ID + key) and an entry in lyzr_agents.json.
"""
import sys

from lyzr_brain import run_lyzr_band_agent

if __name__ == "__main__":
    config_name = sys.argv[1].lower()
    run_lyzr_band_agent(config_name, config_name.capitalize())

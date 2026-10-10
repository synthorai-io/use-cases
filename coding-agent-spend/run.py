"""Run Claude Code on a small repository through the gateway, and print what the run cost.

    python run.py                          one run
    python run.py --hints                  the same, with Claude Code's gateway hint headers on
    python run.py --label repo=shipping --label _user=dev_1
                                           the same, labelled so cost can be split by repo and developer

The agent works on a fresh copy of sample-repo/ in a temporary directory, so the
repository in this folder is never changed. The gateway address and key are set
only in the environment of the child process: nothing in your shell profile or
your Claude Code settings is touched.

Every run has two limits. Claude Code stops the run at --max-budget-usd, and this
script keeps a ledger in .data/spend.json and refuses to start a run that could
take the total past SPEND_CEILING.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE / ".env")
load_dotenv(HERE.parent / ".env")

# Claude Code appends /v1/messages itself, so the address has no /v1.
GATEWAY = os.environ.get("ANTHROPIC_GATEWAY_URL", "https://synthorai.io")
MODEL = os.environ.get("CODING_MODEL", "claude-sonnet-5-5")
RUN_BUDGET = float(os.environ.get("RUN_BUDGET_USD", "0.60"))
SPEND_CEILING = float(os.environ.get("SPEND_CEILING_USD", "2.00"))
RUN_TIMEOUT = 900        # seconds; a run takes about half a minute
LEDGER = HERE / ".data" / "spend.json"

TASK = (
    "One test in this repository fails. Work in three steps. "
    "1. Use the Explore subagent to find where shipping cost is computed and where the "
    "free-shipping rule lives. "
    "2. Fix the bug with the smallest change you can. "
    "3. Use the test-runner subagent to run the tests and report the result. "
    "Finish with one sentence saying what was wrong."
)

# A custom subagent, so the run has one agent type we named ourselves next to
# the built-in ones.
AGENTS = {
    "test-runner": {
        "description": "Runs the test suite and reports which tests pass and fail.",
        "prompt": "Run `python3 -m unittest discover -s tests` and report the result in two sentences. Do not edit files.",
        "tools": ["Bash"],
    },
}


def spent_so_far() -> float:
    return sum(r["cost_usd"] for r in json.loads(LEDGER.read_text())) if LEDGER.exists() else 0.0


def record(row: dict):
    LEDGER.parent.mkdir(exist_ok=True)
    rows = json.loads(LEDGER.read_text()) if LEDGER.exists() else []
    LEDGER.write_text(json.dumps(rows + [row], indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hints", action="store_true",
                    help="set CLAUDE_CODE_GATEWAY_HINT_HEADERS=1, which adds the call type and tool timing headers")
    ap.add_argument("--label", action="append", default=[], metavar="KEY=VALUE",
                    help="a label for X-Synthorai-Metadata, sent through ANTHROPIC_CUSTOM_HEADERS; repeatable")
    args = ap.parse_args()

    bad = [pair for pair in args.label if "=" not in pair or pair.startswith("=")]
    if bad:
        ap.error(f"--label takes KEY=VALUE, got: {', '.join(bad)}")
    if not os.environ.get("SYNTHORAI_API_KEY"):
        sys.exit("SYNTHORAI_API_KEY is not set. Copy .env.example to .env at the repo root and put your key in it.")
    if not shutil.which("claude"):
        sys.exit("The `claude` command was not found. Install Claude Code first: https://claude.com/claude-code")

    spent = spent_so_far()
    if spent + RUN_BUDGET > SPEND_CEILING:
        sys.exit(f"${spent:.4f} spent so far; another run of up to ${RUN_BUDGET:.2f} would pass the "
                 f"${SPEND_CEILING:.2f} ceiling. Raise SPEND_CEILING_USD or delete {LEDGER} to go on.")

    workdir = Path(tempfile.mkdtemp(prefix="coding-agent-spend-")) / "shipping"
    shutil.copytree(HERE / "sample-repo", workdir)

    # Start from the caller's environment minus anything that already points Claude Code
    # somewhere, then add the gateway for this one process.
    # The agent can run commands, so it also gets none of the other keys in your .env.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE", "SYNTHORAI_"))}
    env["ANTHROPIC_BASE_URL"] = GATEWAY
    env["ANTHROPIC_API_KEY"] = os.environ["SYNTHORAI_API_KEY"]
    # An empty configuration directory of its own: the run sees none of your plugins, MCP
    # servers, memory or settings, and leaves no session files in your real one. (--bare would
    # also isolate it, but it removes the subagent tool, and subagents are the point here.)
    env["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp(prefix="coding-agent-spend-config-")
    if args.hints:
        env["CLAUDE_CODE_GATEWAY_HINT_HEADERS"] = "1"
    labels = dict(pair.split("=", 1) for pair in args.label)
    if labels:
        env["ANTHROPIC_CUSTOM_HEADERS"] = "X-Synthorai-Metadata: " + json.dumps(labels, separators=(",", ":"))

    command = [
        "claude", "-p", TASK,
        "--model", MODEL,
        "--agents", json.dumps(AGENTS),
        "--permission-mode", "acceptEdits",
        "--allowedTools", "Bash(python3 -m unittest:*)",
        "--max-budget-usd", f"{RUN_BUDGET:.2f}",
        "--output-format", "json",
    ]
    print(f"running Claude Code on {workdir}")
    print(f"  model {MODEL}   hints {'on' if args.hints else 'off'}   labels {labels or 'none'}   "
          f"limit ${RUN_BUDGET:.2f} (ledger ${spent:.4f} of ${SPEND_CEILING:.2f})")
    t0 = time.time()
    out, problem = None, ""
    try:
        proc = subprocess.run(command, cwd=workdir, env=env, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        out = json.loads(proc.stdout)
    except subprocess.TimeoutExpired:
        problem = f"Claude Code did not finish in {RUN_TIMEOUT} s"
    except json.JSONDecodeError:
        problem = f"Claude Code did not return JSON (exit {proc.returncode}):\n{proc.stdout[:600]}\n{proc.stderr[:600]}"
    finally:
        shutil.rmtree(env["CLAUDE_CONFIG_DIR"], ignore_errors=True)     # the run's own session files
        if out is None:
            # What the run spent is unknown, so the ledger takes the most it could have spent.
            record({"session_id": None, "cost_usd": RUN_BUDGET, "hints": args.hints, "labels": labels,
                    "note": "no result; counted at the run limit", "model": MODEL})
    if out is None:
        sys.exit(problem)
    wall = time.time() - t0

    cost = out.get("total_cost_usd") or 0.0
    record({"session_id": out.get("session_id"), "cost_usd": cost, "hints": args.hints, "labels": labels,
            "turns": out.get("num_turns"), "wall_s": round(wall, 1), "model": MODEL})

    print(f"\nsession {out.get('session_id')}")
    print(f"  {'failed' if out.get('is_error') else 'finished'} in {wall:.0f} s, {out.get('num_turns')} turns, "
          f"${cost:.4f} by Claude Code's own count")
    for model, use in (out.get("modelUsage") or {}).items():
        print(f"  {model:28s} {use.get('inputTokens', 0):6d} in  {use.get('cacheReadInputTokens', 0):7d} cache read  "
              f"{use.get('cacheCreationInputTokens', 0):6d} cache write  {use.get('outputTokens', 0):5d} out  "
              f"${use.get('costUSD', 0):.4f}")
    print(f"\n{(out.get('result') or '').strip()[:500]}")

    tests = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests"],
                           cwd=workdir, capture_output=True, text=True)
    print(f"\ntests in the agent's copy: {'pass' if tests.returncode == 0 else 'FAIL'}  ({workdir})")
    print("\nOpen Traces in the console and search for the session id above: the session page splits "
          "this run's calls and cost by agent.")
    if out.get("is_error"):
        sys.exit(1)


if __name__ == "__main__":
    main()

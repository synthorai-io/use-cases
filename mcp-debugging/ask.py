"""Ask an agent about your request log, through the gateway's MCP server, without opening the console.

    python ask.py                      ask the three questions in QUESTIONS
    python ask.py "your own question"  ask one of your own

Claude Code is the agent. It connects to the MCP server described in .mcp.json
with a read-only admin key, calls the log tools, and answers. Each answer is
saved to .data/transcripts/ with the tools the agent called. The runs we
committed are in transcripts/, for comparison.

Two keys are involved and they do different jobs:

  SYNTHORAI_ADMIN_KEY  reads the request log. It cannot call a model. Give it
                       the logs.read scope and nothing else.
  SYNTHORAI_API_KEY    pays for the agent's own model calls.

Both are set only in the environment of the process this script starts.
"""
import argparse
import json
import os
import re
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

GATEWAY = os.environ.get("ANTHROPIC_GATEWAY_URL", "https://synthorai.io")
MODEL = os.environ.get("AGENT_MODEL", "claude-sonnet-5-5")
RUN_BUDGET = float(os.environ.get("RUN_BUDGET_USD", "0.40"))
SPEND_CEILING = float(os.environ.get("SPEND_CEILING_USD", "2.00"))
RUN_TIMEOUT = 600        # seconds; a question takes well under a minute
LEDGER = HERE / ".data" / "spend.json"

# What the agent is told before every question. The window keeps it on the
# requests the agent-tracing demo made; change it to ask about your own.
WINDOW = os.environ.get("LOG_WINDOW", "2026-10-08T05:54:00Z to 2026-10-08T06:00:00Z")
BRIEF = (
    "You are looking into a support agent's requests with the synthorai tools. "
    f"Only look at the time range {WINDOW}, and only at requests whose metadata has "
    "feature = support-agent: ignore everything else in the workspace and do not mention it. "
    "Use the tools, then answer in at most six sentences with the numbers that support the answer "
    "(costs in dollars, token counts, session ids). If the tools cannot answer part of the question, say so."
)

QUESTIONS = {
    "most-expensive-session": (
        "Which session with the label case = cache-miss, case = context-growth or case = replay-mismatch "
        "cost the most, and what in its token counts explains the cost?"),
    "cache-miss-before-after": (
        "For the label case = cache-miss, compare variant = before with variant = after: "
        "input tokens, cached tokens and cost of each."),
    "failed-requests-by-user": (
        "Did any request with the label _user = u_1001 fail? Give the status code, the error "
        "and how many times it happened. Then say whether _user = u_1002 had any failures."),
}


def spent_so_far() -> float:
    return sum(r["cost_usd"] for r in json.loads(LEDGER.read_text())) if LEDGER.exists() else 0.0


def record(row: dict):
    LEDGER.parent.mkdir(exist_ok=True)
    rows = json.loads(LEDGER.read_text()) if LEDGER.exists() else []
    LEDGER.write_text(json.dumps(rows + [row], indent=1))


def ask(name: str, question: str) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE", "SYNTHORAI_"))}
    env["ANTHROPIC_BASE_URL"] = GATEWAY
    env["ANTHROPIC_API_KEY"] = os.environ["SYNTHORAI_API_KEY"]
    env["SYNTHORAI_ADMIN_KEY"] = os.environ["SYNTHORAI_ADMIN_KEY"]      # read by .mcp.json
    # An empty configuration directory and an empty working directory, both thrown away after
    # the run: the agent sees none of your own Claude Code setup and none of your files.
    config_dir = env["CLAUDE_CONFIG_DIR"] = tempfile.mkdtemp(prefix="mcp-debugging-config-")
    workdir = tempfile.mkdtemp(prefix="mcp-debugging-")
    command = [
        "claude", "-p", f"{BRIEF}\n\nQuestion: {question}",
        "--model", MODEL,
        "--mcp-config", str(HERE / ".mcp.json"), "--strict-mcp-config",
        "--allowedTools", "mcp__synthorai",          # the MCP tools and nothing else: no shell, no files
        "--disallowedTools", "Bash", "Edit", "Write", "Read", "Glob", "Grep", "Agent", "WebFetch", "WebSearch",
        "--max-budget-usd", f"{RUN_BUDGET:.2f}",
        "--output-format", "stream-json", "--verbose",
    ]
    t0 = time.time()
    stdout, problem = "", ""
    try:
        proc = subprocess.run(command, cwd=workdir, env=env, capture_output=True, text=True, timeout=RUN_TIMEOUT)
        stdout, problem = proc.stdout, f"(exit {proc.returncode}):\n{proc.stdout[-600:]}\n{proc.stderr[-600:]}"
    except subprocess.TimeoutExpired:
        problem = f": it did not finish in {RUN_TIMEOUT} s"
    finally:
        for d in (config_dir, workdir):
            shutil.rmtree(d, ignore_errors=True)
    calls, result = [], {}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "assistant":
            for block in event["message"].get("content", []):
                if block.get("type") == "tool_use":
                    calls.append({"tool": block["name"].replace("mcp__synthorai__", ""), "arguments": block["input"]})
        elif event.get("type") == "result":
            result = event
    if not result:
        # What the run spent is unknown, so the ledger takes the most it could have spent.
        record({"name": name, "cost_usd": RUN_BUDGET, "model": MODEL, "note": "no result; counted at the run limit"})
        sys.exit(f"no result from Claude Code {problem}")
    return {"name": name, "question": question, "calls": calls, "answer": (result.get("result") or "").strip(),
            "cost_usd": result.get("total_cost_usd") or 0.0, "turns": result.get("num_turns"),
            "wall_s": round(time.time() - t0, 1), "is_error": bool(result.get("is_error"))}


UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")


def scrub(text: str) -> str:
    """Ids the agent did not need to show do not belong in a saved transcript.

    A question with no label to filter on makes the agent read sessions from the rest of the
    workspace. The demo's own sessions and traces have readable ids (cache-miss-..., trace-...),
    so anything shaped like a UUID is either somebody else's session or a request id, and
    neither is needed to read the answer.
    """
    return UUID.sub("<id removed>", text)


def save(run: dict, folder: Path) -> Path:
    lines = [f"# {run['name']}", "", f"**Question.** {run['question']}", "",
             f"**Tools the agent called** ({len(run['calls'])}):", ""]
    lines += [scrub(f"{i}. `{c['tool']}` {json.dumps(c['arguments'], separators=(', ', ': '))}")
              for i, c in enumerate(run["calls"], 1)] or ["none"]
    lines += ["", "**Answer.**", "", scrub(run["answer"]), "",
              f"_{run['turns']} turns, {run['wall_s']} s, ${run['cost_usd']:.4f} for the agent's own model calls "
              f"on {MODEL}._", ""]
    out = folder / f"{run['name']}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="*", help="a question of your own; without one, the three in QUESTIONS are asked")
    ap.add_argument("--reference", action="store_true",
                    help="save to transcripts/, replacing the committed runs, instead of .data/transcripts/")
    args = ap.parse_args()
    folder = HERE / "transcripts" if args.reference else HERE / ".data" / "transcripts"
    for key in ("SYNTHORAI_API_KEY", "SYNTHORAI_ADMIN_KEY"):
        if not os.environ.get(key):
            sys.exit(f"{key} is not set. Put it in .env at the repo root; the README says what each key is for.")
    if not shutil.which("claude"):
        sys.exit("The `claude` command was not found. Install Claude Code first: https://claude.com/claude-code")
    asked = {"your-question": " ".join(args.question)} if args.question else QUESTIONS
    failed = False
    for name, question in asked.items():
        spent = spent_so_far()
        if spent + RUN_BUDGET > SPEND_CEILING:
            sys.exit(f"${spent:.4f} spent so far; another run of up to ${RUN_BUDGET:.2f} could pass the "
                     f"${SPEND_CEILING:.2f} ceiling. Raise SPEND_CEILING_USD or delete {LEDGER} to go on.")
        print(f"\n{name}\n  Q: {question}")
        run = ask(name, question)
        record({"name": name, "cost_usd": run["cost_usd"], "model": MODEL})
        for c in run["calls"]:
            print(f"  -> {c['tool']} {json.dumps(c['arguments'])[:110]}")
        answer = " ".join(run["answer"].split())
        print(f"  A: {answer[:700]}")
        if run["is_error"]:
            failed = True
            print(f"  (Claude Code reported an error; the text above is its message. ${run['cost_usd']:.4f}, not saved)")
            continue
        print(f"  ({run['turns']} turns, {run['wall_s']} s, ${run['cost_usd']:.4f}; "
              f"saved to {save(run, folder).relative_to(HERE)})")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()

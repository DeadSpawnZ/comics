"""Summarize token usage of past Claude Code subagent runs for this project.

Reads the subagent transcripts that Claude Code stores under
``~/.claude/projects/<project>/<session>/subagents/`` and prints, per run,
the number of assistant turns, the cached context re-read (the dominant cost
driver: every turn re-sends the whole conversation) and how many tool calls
went to exploring code versus running tests or editing.

Usage:
    .venv/Scripts/python.exe .claude/scripts/agent_usage.py [agent_type] [--since YYYY-MM-DD]
"""

import argparse
import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path.home() / ".claude" / "projects" / "c--Users-galeo-Desktop-Comi-Project"
EXPLORE_COMMANDS = {"sed", "grep", "cat", "head", "tail", "ls", "find", "rg"}


def classify_bash(command: str) -> str:
    """Bucket a Bash command into codegraph / test / explore / other."""
    if "codegraph" in command:
        return "codegraph"
    if "manage.py test" in command:
        return "test"
    stripped = re.sub(r"^cd [^&]+&&\s*", "", command.strip())
    first = re.split(r"[\s|;&]+", stripped)[0]
    return "explore" if first in EXPLORE_COMMANDS else "other"


def summarize(transcript: Path) -> tuple[int, int, int, Counter[str]]:
    """Return (turns, cache_read_tokens, output_tokens, tool buckets) for one run."""
    turns = cache_read = output = 0
    buckets: Counter[str] = Counter()
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message") or {}
        usage = message.get("usage") or {}
        turns += 1
        cache_read += usage.get("cache_read_input_tokens", 0)
        output += usage.get("output_tokens", 0)
        for block in message.get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block["name"]
            if name == "Bash":
                buckets[classify_bash(block["input"].get("command", ""))] += 1
            elif name in {"Read", "Grep", "Glob"}:
                buckets["explore"] += 1
            elif name.startswith("mcp__codegraph"):
                buckets["codegraph"] += 1
            elif name in {"Edit", "Write"}:
                buckets["edit"] += 1
    return turns, cache_read, output, buckets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("agent_type", nargs="?", default="backend-senior")
    parser.add_argument("--since", type=datetime.fromisoformat, default=None)
    args = parser.parse_args()

    rows = []
    for meta_path in PROJECT_DIR.glob("*/subagents/*.meta.json"):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if meta.get("agentType") != args.agent_type:
            continue
        transcript = meta_path.with_name(meta_path.name.replace(".meta.json", ".jsonl"))
        modified = datetime.fromtimestamp(transcript.stat().st_mtime)
        if args.since and modified < args.since:
            continue
        rows.append((modified, meta.get("description", "?"), *summarize(transcript)))

    keys = ("explore", "codegraph", "test", "edit", "other")
    for modified, description, turns, cache_read, output, buckets in sorted(rows):
        counts = " ".join(f"{k}={buckets[k]}" for k in keys)
        print(
            f"{modified:%Y-%m-%d} {description[:42]:42} turns={turns:4} "
            f"cache_read={cache_read / 1e6:6.1f}M out={output / 1e3:5.0f}k {counts}"
        )
    if rows:
        total = sum(r[3] for r in rows) / 1e6
        print(f"\n{len(rows)} runs, cache_read total {total:.1f}M, per run {total / len(rows):.1f}M")


if __name__ == "__main__":
    main()

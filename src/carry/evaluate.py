"""`carry eval`: measure recall against a labelled question set kept as a Markdown table.

The set lives with the owner's notes, never in this repository. One row per question:

    | id | question | answerable | expected | ... | [x] |

`answerable` is yes, partial or no. `expected` names the files that answer, best first: a
`[[wikilink]]` matches a file of that basename in any source, a backticked `source:path`
matches that exact file. A row whose last cell is `[x]` is confirmed by the owner; `[ ]` is
provisional. Every other column is ignored.

A run can be saved and replayed: replay recomputes the payload an assistant would receive
with the current formatter, without searching (or calling a judge) again.
"""
import json
import re
import time
from pathlib import Path

ROW = re.compile(r"^\|\s*([A-Za-z0-9_-]+)\s*\|(.*)\|\s*$")
WIKILINK = re.compile(r"\[\[([^\]|#]+)")
PINNED = re.compile(r"`([A-Za-z0-9_-]+):([^`]+)`")


def _cells(line):
    return [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]


def load_set(path):
    """Questions from the first Markdown table whose rows start with an id cell."""
    questions = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if not ROW.match(line):
            continue
        cells = _cells(line)
        if len(cells) < 4 or cells[2].lower() not in ("yes", "partial", "no"):
            continue
        expected = [("*", name.strip()) for name in WIKILINK.findall(cells[3])]
        expected += [(sid, p.strip()) for sid, p in PINNED.findall(cells[3])]
        questions.append(dict(id=cells[0], query=cells[1].replace("\\|", "|"),
                              answerable=cells[2].lower(), expected=expected,
                              confirmed=cells[-1].lower() in ("[x]", "x", "✓")))
    return questions


def _matches(expected, source_id, path):
    stem = path.rsplit("/", 1)[-1]
    stem = stem[:-3] if stem.endswith(".md") else stem
    for sid, name in expected:
        if sid == "*" and name == stem:
            return True
        if sid == source_id and name == path:
            return True
    return False


def _norm(text):
    return " ".join(str(text).lower().split())


def is_echo(item, query):
    """A chat log passage that holds the question itself: the question asked, not an answer."""
    kind = str((item.get("metadata") or {}).get("type", "")).lower()
    raw = kind == "chat-raw" or "chat-raw" in item.get("path", "")
    q = _norm(query)
    return raw and len(q) >= 12 and q in _norm(item.get("text", ""))


def payload(result):
    """What an assistant receives over MCP for this result, in characters, and where it goes."""
    from .mcp_server import GROUNDING, PASSAGE_METADATA, _format_recall
    text, _ = _format_recall(result)
    evidence = result.get("evidence", [])
    passages = sum(len(e.get("text", "")) for e in evidence)
    metadata, seen = 0, set()
    for e in evidence:
        shown = {k: v for k, v in (e.get("metadata") or {}).items() if k in PASSAGE_METADATA}
        if shown and (e["source_id"], e["path"]) not in seen:
            metadata += len(json.dumps(shown, ensure_ascii=False, default=str))
        seen.add((e["source_id"], e["path"]))
    state = next((len(p) for p in text.split("\n\n") if p.startswith("Search state: ")), 0)
    return dict(total=len(text), grounding=len(GROUNDING), state=state, passages=passages,
                metadata=metadata, other=len(text) - len(GROUNDING) - state - passages - metadata)


def score(question, result):
    """Rank of the first expected file among the files the evidence names, in order."""
    files = []
    for item in result.get("evidence", []):
        key = (item["source_id"], item["path"])
        if key not in files:
            files.append(key)
    rank = next((i for i, (sid, p) in enumerate(files, 1) if _matches(question["expected"], sid, p)), None)
    diagnostics = result.get("diagnostics", {})
    return dict(id=question["id"], answerable=question["answerable"], confirmed=question["confirmed"],
                status=result.get("status"), rank=rank, files=[f"{s}:{p}" for s, p in files],
                echo=sum(is_echo(e, question["query"]) for e in result.get("evidence", [])),
                payload=payload(result), elapsed_ms=diagnostics.get("elapsed_ms"),
                judge_usage=diagnostics.get("judge_usage"),
                best_confidence=diagnostics.get("best_confidence"))


def summarize(rows):
    def block(subset):
        n = len(subset)
        if not n:
            return dict(n=0)
        ranks = [r["rank"] for r in subset]
        return dict(n=n, hit1=sum(r == 1 for r in ranks), hit3=sum(bool(r) and r <= 3 for r in ranks),
                    mrr=round(sum(1 / r for r in ranks if r) / n, 3),
                    false_none=sum(r["status"] == "no_evidence" for r in subset))
    answerable = [r for r in rows if r["answerable"] != "no"]
    unanswerable = [r for r in rows if r["answerable"] == "no"]
    totals = [r["payload"]["total"] for r in rows]
    usage = [r["judge_usage"] or {} for r in rows]
    return dict(
        questions=len(rows), confirmed=sum(r["confirmed"] for r in rows),
        answerable=block(answerable), yes=block([r for r in answerable if r["answerable"] == "yes"]),
        abstain=dict(n=len(unanswerable), correct=sum(r["status"] == "no_evidence" for r in unanswerable)),
        echo_questions=sum(bool(r["echo"]) for r in rows),
        payload_mean=round(sum(totals) / max(len(totals), 1)), payload_max=max(totals, default=0),
        payload_parts={k: round(sum(r["payload"][k] for r in rows) / max(len(rows), 1))
                       for k in ("grounding", "state", "metadata", "passages", "other")},
        elapsed_ms_mean=round(sum(r["elapsed_ms"] or 0 for r in rows) / max(len(rows), 1)),
        judge_input_tokens=sum(u.get("input_tokens", 0) for u in usage),
        judge_output_tokens=sum(u.get("output_tokens", 0) for u in usage))


def run(workspace, questions, budget=None, save=None):
    """Search every question once; optionally keep the raw results for replay."""
    from .recall import recall
    rows, raw = [], {}
    for question in questions:
        result = recall(workspace, question["query"], budget=budget)
        raw[question["id"]] = result
        rows.append(score(question, result))
    if save:
        Path(save).write_text(json.dumps(raw, ensure_ascii=False, default=str), encoding="utf-8")
    return dict(at=time.strftime("%Y-%m-%dT%H:%M:%S"), summary=summarize(rows), rows=rows)


def replay(questions, saved):
    """Score saved results again with the current code (formatter, echo rule) without searching."""
    raw = json.loads(Path(saved).read_text(encoding="utf-8"))
    rows = [score(q, raw[q["id"]]) for q in questions if q["id"] in raw]
    return dict(at=time.strftime("%Y-%m-%dT%H:%M:%S"), replayed=str(saved), summary=summarize(rows), rows=rows)

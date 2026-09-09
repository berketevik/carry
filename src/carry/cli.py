"""Carry command line.

Every command takes an explicit workspace, either with --workspace or through
CARRY_WORKSPACE. There is no implicit default folder: an agent memory tool that
guesses where to write is a bug.
"""
import argparse
import json
import os
import sys
from pathlib import Path

from . import index as index_module
from . import store
from .config import EmbeddingConfig, RetrievalConfig, SourceConfig, Workspace, open_workspace
from .errors import CarryError, RevisionConflict
from .fixtures import install_fixture
from .recall import recall
from .status import status

EXIT_OK, EXIT_FAILED, EXIT_NO_EVIDENCE = 0, 1, 2


def _print(payload, as_json, lines=()):
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    else:
        for line in lines:
            print(line)


def _source(spec, writable=False, scope="personal"):
    if "=" not in spec:
        raise CarryError("source_spec_must_be_id=path")
    source_id, path = spec.split("=", 1)
    return SourceConfig(source_id=source_id.strip(), root=Path(path).expanduser(),
                        writable=writable, scope=scope)


def cmd_init(args):
    sources = [_source(s) for s in (args.source or [])]
    sources += [_source(s, writable=True) for s in (args.writable_source or [])]
    embedding = EmbeddingConfig(provider=args.embedding, model=args.embed_model)
    workspace = Workspace.create(args.workspace, sources=sources, embedding=embedding,
                                 retrieval=RetrievalConfig(), exist_ok=args.force)
    _print(workspace.to_json(), args.json,
           [f"workspace initialised at {workspace.state_dir}",
            f"sources: {', '.join(s.source_id for s in workspace.sources) or 'none'}",
            f"embedding provider: {workspace.embedding.provider}"])
    return EXIT_OK


def cmd_source(args):
    workspace = open_workspace(args.workspace)
    if args.source_command == "list":
        payload = [s.to_json() for s in workspace.sources]
        _print(payload, args.json,
               [f"{s.source_id}\t{'rw' if s.writable else 'ro'}\t{s.scope}\t{s.root}"
                for s in workspace.sources] or ["no sources configured"])
        return EXIT_OK
    new = SourceConfig(source_id=args.id, root=Path(args.root).expanduser(),
                       writable=args.writable, scope=args.scope)
    workspace = workspace.with_sources(list(workspace.sources) + [new]).save()
    _print(workspace.to_json(), args.json, [f"source added: {new.source_id} ({new.root})"])
    return EXIT_OK


def cmd_index(args):
    workspace = open_workspace(args.workspace)
    result = index_module.build(workspace)
    _print(result, args.json, [json.dumps(result, ensure_ascii=False, default=str)])
    return EXIT_OK if result["status"] in ("built", "reindexing") else EXIT_FAILED


def cmd_status(args):
    workspace = open_workspace(args.workspace)
    payload = status(workspace, probe_provider=args.probe)
    lines = [f"index: {payload['index']['state']} "
             f"({payload['index']['indexed_files']}/{payload['index']['corpus_files']} files"
             f", usable={payload['index']['usable']})",
             f"embedding: {payload['embedding']['name']} "
             f"(semantic={payload['embedding'].get('semantic')})",
             f"sources: {len(payload['workspace']['sources'])}",
             f"degradation: {', '.join(payload['degradation']) or 'none'}"]
    _print(payload, args.json, lines)
    return EXIT_OK if payload["ok"] else EXIT_FAILED


def cmd_recall(args):
    workspace = open_workspace(args.workspace)
    budget = {}
    if args.top_k:
        budget["top_k"] = args.top_k
    if args.max_chars:
        budget["max_chars"] = args.max_chars
    result = recall(workspace, args.query, source_ids=args.source or None,
                    budget=budget or None, include_history=args.history)
    lines = [f"status: {result['status']}"]
    for number, item in enumerate(result.get("evidence", []), 1):
        lines.append(f"[{number}] {item['citation']} > {item['heading']} "
                     f"[{item['record_id']} r{item['revision']} {item['state']}]")
        lines.append("    " + " ".join(item["text"].split())[:240])
    diagnostics = result.get("diagnostics", {})
    missing = diagnostics.get("unmatched_terms") or []
    if missing:
        lines.append("terms not present in the corpus: " + ", ".join(missing))
    warnings = diagnostics.get("warnings") or []
    if warnings:
        lines.append("warnings: " + ", ".join(warnings))
    if result.get("error"):
        lines.append("error: " + str(result["error"]))
    _print(result, args.json, lines)
    if not result["ok"]:
        return EXIT_FAILED
    return EXIT_OK if result["status"] == "evidence" else EXIT_NO_EVIDENCE


def cmd_record(args):
    workspace = open_workspace(args.workspace)
    if args.record_command == "add":
        content = args.content if args.content is not None else sys.stdin.read()
        result = store.write_record(workspace, args.title, content, event_id=args.event or "",
                                    state=args.state, author=args.author or "",
                                    surface=args.surface or "", source_id=args.source)
        _print(result, args.json,
               [("duplicate event, existing record: " if result.get("duplicate") else "written: ")
                + f"{result['record_id']} r{result['revision']} {result['state']} "
                + f"{result['source_id']}:{result['path']}"])
        return EXIT_OK
    try:
        if args.record_command == "accept":
            result = store.set_state(workspace, args.id, "accepted", args.expect_revision)
        else:
            content = args.content if args.content is not None else sys.stdin.read()
            result = store.supersede(workspace, args.id, content, args.expect_revision,
                                     title=args.title)
    except RevisionConflict as exc:
        _print(dict(status="conflict", detail=str(exc)), args.json, ["conflict: " + str(exc)])
        return EXIT_FAILED
    _print(result, args.json, [json.dumps(result, ensure_ascii=False, default=str)])
    return EXIT_OK


def cmd_demo(args):
    """Install the frozen synthetic corpus and a workspace that indexes it."""
    corpus = Path(args.into).expanduser()
    notes = install_fixture(corpus / "cedar", name=args.fixture, exist_ok=args.force)
    sources = [SourceConfig(source_id="cedar", root=notes, writable=False, scope="personal")]
    if args.writable:
        writable_root = corpus / "carry-records"
        writable_root.mkdir(parents=True, exist_ok=True)
        sources.append(SourceConfig(source_id="records", root=writable_root, writable=True))
    workspace = Workspace.create(args.workspace, sources=sources,
                                 embedding=EmbeddingConfig(provider=args.embedding),
                                 exist_ok=args.force)
    result = index_module.build(workspace)
    _print(dict(workspace=str(workspace.state_dir), corpus=str(notes), index=result), args.json,
           [f"corpus installed at {notes}", f"workspace at {workspace.state_dir}",
            "index: " + json.dumps(result, default=str)])
    return EXIT_OK if result["status"] == "built" else EXIT_FAILED


def build_parser():
    parser = argparse.ArgumentParser(prog="carry", description="Portable local memory")
    parser.add_argument("--workspace", default=os.environ.get("CARRY_WORKSPACE"),
                        help="workspace state directory (or CARRY_WORKSPACE)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a workspace")
    init.add_argument("--source", action="append", metavar="ID=PATH", help="read-only source")
    init.add_argument("--writable-source", action="append", metavar="ID=PATH")
    init.add_argument("--embedding", default="ollama", choices=("ollama", "hashing"))
    init.add_argument("--embed-model", default="nomic-embed-text")
    init.add_argument("--force", action="store_true", help="reuse an existing workspace")
    init.set_defaults(func=cmd_init)

    source = sub.add_parser("source", help="manage sources")
    source_sub = source.add_subparsers(dest="source_command", required=True)
    source_sub.add_parser("list")
    add = source_sub.add_parser("add")
    add.add_argument("--id", required=True)
    add.add_argument("--root", required=True)
    add.add_argument("--writable", action="store_true")
    add.add_argument("--scope", default="personal", choices=("personal", "team"))
    source.set_defaults(func=cmd_source)

    sub.add_parser("index", help="rebuild the derived index").set_defaults(func=cmd_index)

    st = sub.add_parser("status", help="integration and index status")
    st.add_argument("--probe", action="store_true", help="contact the embedding provider")
    st.set_defaults(func=cmd_status)

    rc = sub.add_parser("recall", help="retrieve cited evidence")
    rc.add_argument("query")
    rc.add_argument("--source", action="append", help="restrict to a source id")
    rc.add_argument("--top-k", type=int)
    rc.add_argument("--max-chars", type=int)
    rc.add_argument("--history", action="store_true", help="include superseded revisions")
    rc.set_defaults(func=cmd_recall)

    record = sub.add_parser("record", help="write and review records")
    record_sub = record.add_subparsers(dest="record_command", required=True)
    ra = record_sub.add_parser("add")
    ra.add_argument("--title", required=True)
    ra.add_argument("--content", help="content, or read stdin when omitted")
    ra.add_argument("--event", help="event id for idempotent writes")
    ra.add_argument("--state", default="draft", choices=("draft", "accepted"))
    ra.add_argument("--author")
    ra.add_argument("--surface")
    ra.add_argument("--source", help="writable source id")
    acc = record_sub.add_parser("accept")
    acc.add_argument("--id", required=True)
    acc.add_argument("--expect-revision", type=int, required=True)
    cor = record_sub.add_parser("correct")
    cor.add_argument("--id", required=True)
    cor.add_argument("--expect-revision", type=int, required=True)
    cor.add_argument("--content")
    cor.add_argument("--title")
    record.set_defaults(func=cmd_record)

    demo = sub.add_parser("demo", help="install the synthetic corpus and index it")
    demo.add_argument("--into", required=True, help="folder for the synthetic corpus")
    demo.add_argument("--fixture", default="cedar")
    demo.add_argument("--embedding", default="ollama", choices=("ollama", "hashing"))
    demo.add_argument("--writable", action="store_true", help="also create a records source")
    demo.add_argument("--force", action="store_true")
    demo.set_defaults(func=cmd_demo)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except CarryError as exc:
        payload = dict(status="error", error=type(exc).__name__, detail=str(exc))
        _print(payload, args.json, [f"error: {type(exc).__name__}: {exc}"])
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())

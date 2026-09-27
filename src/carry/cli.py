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
from . import store, capture, lifecycle
from . import vault as vault_module
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
             f"capture: {payload['capture']['state']}",
             f"degradation: {', '.join(payload['degradation']) or 'none'}"]
    _print(payload, args.json, lines)
    return EXIT_OK if payload["ok"] else EXIT_FAILED


def cmd_recall(args):
    workspace = open_workspace(args.workspace)
    budget = {}
    if args.top_k is not None:
        budget["top_k"] = args.top_k
    if args.max_chars is not None:
        budget["max_chars"] = args.max_chars
    result = recall(workspace, args.query, source_ids=args.source or None,
                    budget=budget or None, include_history=args.history, include_drafts=args.drafts)
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
            result = lifecycle.propose(workspace, content, args.event,
                title=args.title or "Proposed correction", target_id=args.id,
                expected_revision=args.expect_revision, source_id=args.source)

    except RevisionConflict as exc:
        _print(dict(status="conflict", detail=str(exc)), args.json, ["conflict: " + str(exc)])
        return EXIT_FAILED
    _print(result, args.json, [json.dumps(result, ensure_ascii=False, default=str)])
    return EXIT_OK


def cmd_proposal(args):
    workspace = open_workspace(args.workspace)
    command = args.proposal_command
    if command in ("enable", "disable"):
        result = lifecycle.configure_client(workspace, args.client, args.source, command == "enable")
    elif command == "add":
        content = args.content if args.content is not None else sys.stdin.read()
        result = lifecycle.propose(workspace, content, args.event, args.source_ref or [],
            title=args.title, source_id=args.source, target_id=args.target,
            expected_revision=args.expect_target_revision, target_source_id=args.target_source,
            expected_proposal_revision=args.expect_revision)
    elif command == "list":
        result = lifecycle.list_proposals(workspace, include_reviewed=args.all)
    elif command == "show":
        result = lifecycle.review(workspace, args.id)
    else:
        operation = lifecycle.accept if command == "accept" else lifecycle.reject
        result = operation(workspace, args.id, args.expect_revision, args.review_token)
    lines = [json.dumps(result, ensure_ascii=False, indent=2, default=str)]
    if command == "show" and not args.json:
        lines = [f"{result['record_id']} r{result['revision']} {result['state']}",
                 result['diff'], "review token: " + result['review_token']]
        if result['conflict']:
            lines.append("conflict: " + result['conflict'])
    _print(result, args.json, lines)
    return EXIT_FAILED if isinstance(result, dict) and result.get('status') == 'accepted_index_pending' else EXIT_OK


def cmd_capture(args):
    workspace = open_workspace(args.workspace)
    if args.capture_command == "configure":
        result = capture.configure(workspace, args.client, args.source,
                                   args.opt_in_prompts, args.client_bin)
    elif args.capture_command in ("pause", "resume"):
        result = capture.set_paused(workspace, args.client, args.capture_command == "pause")
    elif args.capture_command == "settings":
        result = capture.settings_fragment(workspace, args.client)
    else:
        result = capture.capture_status(workspace)
    _print(result, args.json, [json.dumps(result, ensure_ascii=False, indent=2)])
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


def cmd_github(args):
    from . import github
    from .maintenance import run, start, job_status
    ws = open_workspace(args.workspace)
    if args.github_command == 'account':
        result = github.account()
    elif args.github_command == 'repos':
        result = github.repositories(args.page)
    elif args.github_command == 'add':
        fields = dict(source_id=args.id, repository=args.repository, branch=args.branch, folder=args.folder)
        result = run(ws, 'github_connect', fields) if args.wait else start(ws, 'github_connect', **fields)
    elif args.github_command == 'sync':
        fields = dict(source_id=args.id)
        result = run(ws, 'github_sync', fields) if args.wait else start(ws, 'github_sync', **fields)
    else:
        result = job_status(ws)
    _print(result, args.json, [json.dumps(result, ensure_ascii=False)])
    return EXIT_FAILED if result.get('status') == 'failed' or result.get('state') in ('failed', 'unavailable') else EXIT_OK


def cmd_maintain(args):
    from .maintenance import run
    result = run(open_workspace(args.workspace), 'maintain', {})
    _print(result, args.json, [json.dumps(result)])
    return EXIT_FAILED if result.get('status') == 'failed' else EXIT_OK


def cmd_model(args):
    from .maintenance import run, start
    ws = open_workspace(args.workspace)
    result = run(ws, 'model_setup', dict(model=args.model)) if args.wait else start(ws, 'model_setup', model=args.model)
    _print(result, args.json, [json.dumps(result)])
    return EXIT_FAILED if result.get('status') == 'failed' else EXIT_OK


def cmd_vault(args):
    if args.vault_command == 'rollback':
        result = vault_module.rollback(Path(args.path).expanduser().resolve(), args.id)
        _print(result, args.json, [f"rolled back {args.id}"])
        return EXIT_OK
    plan = vault_module.plan(args.path, language=args.language, workspace=args.workspace,
                             capture=args.capture)
    lines = [f"{c['status']}\t{c['rel']}" for c in plan['changes']] + [f"conflict\t{r}" for r in plan['conflicts']]
    # Conflicting files are never written; the rest still applies, and the exit code reports them.
    status = EXIT_FAILED if plan['conflicts'] else EXIT_OK
    if args.dry_run or not (plan['changes'] or plan['capture']):
        _print(plan, args.json, lines or ['nothing to change'])
        return status
    result = vault_module.apply(plan, git=not args.no_git)
    lines += [f"capture {client}: {state}" for client, state in result['capture'].items()]
    lines += [f"proposals {client}: {state}" for client, state in result.get('proposals', {}).items()]
    _print(result, args.json, lines + [f"vault ready at {result['target']} (journal {result['id']})"])
    return status


def cmd_setup(args):
    from . import setup_wizard
    return setup_wizard.run(state_dir=args.workspace, assume_yes=args.yes, vault_path=args.vault,
                            language=args.language, animation=not args.no_animation)


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
    rc.add_argument("--drafts", action="store_true", help="explicitly include unaccepted proposals")
    rc.set_defaults(func=cmd_recall)

    sub.add_parser('maintain', help='synchronize GitHub and refresh the index').set_defaults(func=cmd_maintain)
    gh = sub.add_parser('github', help='read-only GitHub knowledge sources')
    ghsub = gh.add_subparsers(dest='github_command', required=True)
    ghsub.add_parser('account')
    ghsub.add_parser('repos').add_argument('--page', type=int, default=1)
    ghsub.add_parser('status')
    add = ghsub.add_parser('add')
    add.add_argument('--id', required=True)
    add.add_argument('--repository', required=True)
    add.add_argument('--branch', default='')
    add.add_argument('--folder', default='')
    add.add_argument('--wait', action='store_true')
    sync = ghsub.add_parser('sync')
    sync.add_argument('--id', required=True)
    sync.add_argument('--wait', action='store_true')
    gh.set_defaults(func=cmd_github)
    model = sub.add_parser('model', help='download and activate a local embedding model')
    model.add_argument('model', choices=('keyword_jev', 'keyword_assistant', 'assistant_ranked', 'accurate_multilingual', 'embeddinggemma', 'qwen3-embedding:0.6b', 'nomic-embed-text'))
    model.add_argument('--wait', action='store_true')
    model.set_defaults(func=cmd_model)

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
    cor.add_argument("--event", required=True, help="idempotent proposal event id")
    cor.add_argument("--source", help="writable proposal destination")
    record.set_defaults(func=cmd_record)

    prop = sub.add_parser("proposal", help="create, inspect and review decision proposals")
    psub = prop.add_subparsers(dest="proposal_command", required=True)
    for name in ("enable", "disable"):
        cmd = psub.add_parser(name, help="configure optional MCP proposal writes for a client")
        cmd.add_argument("--client", choices=lifecycle.CLIENTS, required=True)
        cmd.add_argument("--source", required=True)
    add = psub.add_parser("add", help="create a draft or refine the existing captured-event draft")
    add.add_argument("--content", help="synthesized decision text, or read stdin")
    add.add_argument("--title", default="Proposed decision")
    add.add_argument("--event", required=True)
    add.add_argument("--source")
    add.add_argument("--source-ref", action="append")
    add.add_argument("--target")
    add.add_argument("--target-source")
    add.add_argument("--expect-target-revision", type=int)
    add.add_argument("--expect-revision", type=int, help="required when refining an existing draft")
    psub.add_parser("list").add_argument("--all", action="store_true")
    psub.add_parser("show").add_argument("--id", required=True)
    for name in ("accept", "reject"):
        cmd = psub.add_parser(name)
        cmd.add_argument("--id", required=True)
        cmd.add_argument("--expect-revision", type=int, required=True)
        cmd.add_argument("--review-token", required=True, help="token returned by proposal show")
    prop.set_defaults(func=cmd_proposal)

    cap = sub.add_parser("capture", help="opt-in prompt capture and diagnostic receipts")
    cap_sub = cap.add_subparsers(dest="capture_command", required=True)
    cfg = cap_sub.add_parser("configure", help="check client capability and opt in; does not edit client settings")
    cfg.add_argument("--client", required=True, choices=capture.CLIENTS)
    cfg.add_argument("--source", required=True, help="personal writable source id")
    cfg.add_argument("--opt-in-prompts", action="store_true")
    cfg.add_argument("--client-bin", help="CLI executable to probe")
    for name in ("pause", "resume", "settings"):
        cap_sub.add_parser(name).add_argument("--client", required=True, choices=capture.CLIENTS)
    cap_sub.add_parser("status")
    cap.set_defaults(func=cmd_capture)

    vlt = sub.add_parser('vault', help='create or upgrade a vault from the Carry template')
    vsub = vlt.add_subparsers(dest='vault_command', required=True)
    vinit = vsub.add_parser('init', help='write the template; --workspace also wires the Carry MCP server')
    vinit.add_argument('path')
    vinit.add_argument('--dry-run', action='store_true', help='show the file plan without writing')
    vinit.add_argument('--language', default='Turkish', help='language of human-facing notes')
    vinit.add_argument('--no-git', action='store_true', help='do not run git init')
    vinit.add_argument('--capture', action='store_true',
                       help='opt in to whole-prompt capture into sources/carry (needs --workspace)')
    vrb = vsub.add_parser('rollback', help='undo one init by its journal id')
    vrb.add_argument('path')
    vrb.add_argument('--id', required=True)
    vlt.set_defaults(func=cmd_vault)

    setup = sub.add_parser("setup", help="guided setup in the terminal: workspace, vault, search, clients, index")
    setup.add_argument("--yes", action="store_true", help="take every default without asking")
    setup.add_argument("--vault", help="create the vault here")
    setup.add_argument("--language", choices=("Turkish", "English"))
    setup.add_argument("--no-animation", action="store_true")
    setup.set_defaults(func=cmd_setup)

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
    except RevisionConflict as exc:
        _print(dict(status="conflict", detail=str(exc)), args.json, ["conflict: " + str(exc)])
        return EXIT_FAILED
    except CarryError as exc:
        payload = dict(status="error", error=type(exc).__name__, detail=str(exc))
        _print(payload, args.json, [f"error: {type(exc).__name__}: {exc}"])
        return EXIT_FAILED


if __name__ == "__main__":
    sys.exit(main())

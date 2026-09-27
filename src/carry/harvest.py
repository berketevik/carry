"""`carry harvest`: durable items from finished Claude Code / Codex threads of a vault.

Measured design (2026-09-27, 7 real threads against a 142-item reference): Sonnet
extracts items with a verbatim quote; code checks type, quote and speaker; Jev, when
a key is configured, checks support, owner attribution, durability and later
withdrawal, then compares each item with the vault through recall. Output is one
immutable raw per thread under sources/carry/harvest/ and one draft digest in the +/ inbox.
Nothing edits, merges or deletes an existing note; nothing is accepted automatically.
An extraction or judge failure leaves the thread pending for the next run.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from . import jev
from .errors import CarryError
from .mask import mask
from .persistence import atomic_text

STATE_NAME = 'harvest.json'
MAX_TEXT = 6000
WINDOW = 20000
MIN_IDLE_MINUTES = 30
TYPES = ('decision', 'fact', 'preference', 'open_item')
WRITE_TOOLS = ('Write', 'Edit', 'MultiEdit', 'NotebookEdit')

SYSTEM = """You extract what deserves to become a note in the owner's personal knowledge vault from a chat between the owner and an AI assistant. Notes are in {language}.
Return ONLY JSON: {{"items": [{{"type": "decision|fact|preference|open_item", "statement": "<one self-contained sentence in {language}>", "exchange": <i>, "quote": "<verbatim 5-30 words copied exactly from that exchange>", "keywords": ["<3-6 short search terms, in {language} and in English>"]}}]}}
Include: decisions the owner made or approved; durable facts about the owner, devices, projects, people, systems, or measured results and configurations that later work depends on; stated preferences or working rules; open items still pending at the end.
Exclude: transient chatter, the assistant's intermediate reasoning, anything superseded later in the same chat (keep the final state), generic knowledge, duplicates. Never state anything that is not supported by the quote. If nothing is durable, return {{"items": []}}."""

VERIFY = {
    'supported': "Is the candidate's statement supported by what is written in the cited exchange (owner or assistant text)? Plausible but unwritten does not count.",
    'owner_stated': "Did the owner (not the assistant) state, decide or explicitly approve what the candidate says? An assistant suggestion or result the owner did not confirm does not count.",
    'durable': "Is the candidate durable knowledge about the owner, their devices, projects, people or systems that is worth remembering after this chat? Transient chatter, a question without an outcome, or generic knowledge is not.",
    'withdrawn': "Do the later owner messages withdraw, reverse or replace what the candidate says?",
}

LABELS = {
    'Turkish': dict(new='Yeni', conflict='Çelişki adayları (incele)', review='İncelenecek', known='Zaten kayıtlı', resolved='Yapılmış görünüyor',
                    assistant='asistanın önerisi/sonucu; sahibi onaylamadı', decision='karar', fact='olgu',
                    preference='tercih', open_item='açık iş', unchecked='vault ile karşılaştırılmadı (Jev anahtarı yok)'),
    'English': dict(new='New', conflict='Conflict candidates (review)', review='To review', known='Already recorded', resolved='Apparently done',
                    assistant="assistant's suggestion or result; not confirmed by the owner", decision='decision',
                    fact='fact', preference='preference', open_item='open item',
                    unchecked='not compared with the vault (no Jev key)'),
}

norm = lambda s: re.sub(r'\s+', ' ', s or '').strip().lower()


# -- transcripts -------------------------------------------------------------

def _strip(text):
    text = re.sub(r'<system-reminder>.*?</system-reminder>', '', text or '', flags=re.S)
    text = re.sub(r'<local-command-(caveat|stdout)>.*?</local-command-\1>', '', text, flags=re.S)
    return text.strip()


def _harness(text):
    return (not text or text.startswith(('<task-notification', '<agent-message', '<command-name>', '<command-message>',
                                         '[SYSTEM NOTIFICATION', '<recommended_plugins>', '# AGENTS.md',
                                         '<user_instructions>', '<permissions instructions>'))
            or '<environment_context>' in text[:200])


def claude_project_dir(root):
    return Path.home() / '.claude' / 'projects' / re.sub(r'[^A-Za-z0-9]', '-', str(root))


def find_threads(root):
    """(client, thread id, path) for every transcript whose working directory is the vault."""
    root = Path(root).resolve()
    found = []
    project = claude_project_dir(root)
    if project.is_dir():
        found += [('claude', p.stem, p) for p in sorted(project.glob('*.jsonl'))]
    sessions = Path.home() / '.codex' / 'sessions'
    if sessions.is_dir():
        for p in sorted(sessions.rglob('*.jsonl')):
            try:
                with open(p, errors='ignore') as handle:
                    first = json.loads(handle.readline() or '{}')
            except (OSError, ValueError):
                continue
            meta = first.get('payload') or {}
            # Guardian reviews and subagents share the parent's session: only the owner's thread counts.
            if meta.get('parent_thread_id') or meta.get('thread_source', 'user') != 'user':
                continue
            if first.get('type') == 'session_meta' and meta.get('cwd') and Path(meta['cwd']).resolve() == root:
                found.append(('codex', meta.get('id') or p.stem, p))
    return found


def read_thread(client, path, root):
    """Exchanges [{i, user, assistant}] plus whether the thread already wrote notes itself."""
    turns, cur, filed = [], None, False
    notes = [str(Path(root) / d) for d in ('notes', 'log')]
    pending_writes, failed = set(), set()  # a denied or failed write did not file anything
    for line in open(path, errors='ignore'):
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if client == 'claude':
            if d.get('isSidechain') or d.get('isMeta'):
                continue
            m = d.get('message') or {}
            if d.get('type') == 'user':
                c = m.get('content')
                if isinstance(c, list):
                    results = [x for x in c if isinstance(x, dict) and x.get('type') == 'tool_result']
                    if results:
                        failed.update(r.get('tool_use_id') for r in results if r.get('is_error'))
                        continue
                    c = '\n'.join(x.get('text', '') for x in c if isinstance(x, dict) and x.get('type') == 'text')
                t = _strip(c)
                if _harness(t):
                    continue
                cur = dict(user=t, assistant='')
                turns.append(cur)
            elif d.get('type') == 'assistant' and cur is not None:
                blocks = [x for x in m.get('content', []) if isinstance(x, dict)]
                for b in blocks:
                    if b.get('type') == 'tool_use' and b.get('name') in WRITE_TOOLS:
                        target = str((b.get('input') or {}).get('file_path', ''))
                        if any(target.startswith(n) for n in notes):
                            pending_writes.add(b.get('id'))
                texts = [b.get('text', '') for b in blocks if b.get('type') == 'text']
                if any(t.strip() for t in texts):
                    cur['assistant'] = '\n'.join(texts).strip()
        else:
            p = d.get('payload') or {}
            if d.get('type') == 'response_item' and p.get('type') == 'message' and p.get('role') == 'user':
                t = _strip('\n'.join(x.get('text', '') for x in p.get('content', []) if isinstance(x, dict)))
                if _harness(t):
                    continue
                cur = dict(user=t, assistant='')
                turns.append(cur)
            elif d.get('type') == 'event_msg' and p.get('type') == 'task_complete' and cur is not None:
                cur['assistant'] = (p.get('last_agent_message') or '').strip()
            elif d.get('type') == 'response_item' and p.get('type') in ('custom_tool_call', 'function_call'):
                body = json.dumps(p.get('input') or p.get('arguments') or '')
                filed = filed or any(k in body for k in ('Add File: notes/', 'Update File: notes/',
                                                         'Add File: log/', 'Update File: log/'))
    filed = filed or bool(pending_writes - failed)
    exchanges = [dict(i=i + 1, user=mask(t['user'])[0][:MAX_TEXT], assistant=mask(t['assistant'])[0][:MAX_TEXT])
                 for i, t in enumerate(turns) if t['user']]
    return exchanges, filed


# -- extraction ----------------------------------------------------------------

def _windows(exchanges, limit=WINDOW):
    out, cur, size = [], [], 0
    for e in exchanges:
        n = len(e['user']) + len(e['assistant'])
        if cur and size + n > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(e)
        size += n
    return out + ([cur] if cur else [])


def _render(exchanges):
    return '\n\n'.join(f"### Exchange {e['i']}\nOWNER: {e['user']}\nASSISTANT: {e['assistant']}" for e in exchanges)


def extractor():
    """The owner's own client runs the extraction on their plan: Claude Sonnet, else Codex gpt-6-astra (both measured)."""
    if shutil.which('claude'):
        return 'claude:sonnet'
    if shutil.which('codex'):
        return 'codex:gpt-6-astra'  # measured 64% recall, every quote verbatim; gpt-reserve reached 38%
    raise CarryError('no_extraction_client')


def _items_from(text):
    try:
        items = json.loads(text[text.index('{'):text.rindex('}') + 1])['items']
    except (ValueError, KeyError, TypeError):
        return None
    return [x for x in items if isinstance(x, dict)] if isinstance(items, list) else None


def extract_window(exchanges, language, which):
    system = SYSTEM.format(language=language)
    body = _render(exchanges)
    for _ in range(2):
        with tempfile.TemporaryDirectory(prefix='carry-harvest-') as cwd:
            if which.startswith('claude'):
                proc = subprocess.run(['claude', '-p', '--model', which.split(':')[1], '--output-format', 'json',
                                       '--tools', '', '--system-prompt', system, body],
                                      capture_output=True, text=True, timeout=900, cwd=cwd)
                try:
                    text = json.loads(proc.stdout).get('result', '')
                except ValueError:
                    text = ''
            else:
                proc = subprocess.run(['codex', 'exec', '--skip-git-repo-check', '-m', which.split(':')[1],
                                       system + '\n\n' + body], capture_output=True, text=True, timeout=900, cwd=cwd)
                text = proc.stdout
        items = _items_from(text)
        if items is not None:
            return items
    raise CarryError('extraction_unparseable')


# -- checks, verification, comparison with the vault ----------------------------

def provenance(items, exchanges):
    """Type, quote containment and speaker in code; items without a verbatim quote are dropped."""
    by_id = {e['i']: e for e in exchanges}
    seen, kept, dropped = set(), [], 0
    for it in items:
        quote = norm(it.get('quote'))
        where = None
        order = [by_id[it['exchange']]] if it.get('exchange') in by_id else []
        for e in order + list(by_id.values()):
            if quote and quote in norm(e['user']):
                where = ('owner', e['i']); break
            if quote and quote in norm(e['assistant']):
                where = ('assistant', e['i']); break
        digest = hashlib.sha1(norm(it.get('statement')).encode()).hexdigest()[:12]
        if it.get('type') not in TYPES or not where or not norm(it.get('statement')) or digest in seen:
            dropped += 1
            continue
        seen.add(digest)
        keywords = [k.strip() for k in (it.get('keywords') or []) if isinstance(k, str) and k.strip()][:6]
        kept.append(dict(type=it['type'], statement=it['statement'].strip(), quote=it['quote'].strip(),
                         speaker=where[0], exchange=where[1], hash=digest, keywords=keywords))
    return kept, dropped


def verify(item, exchanges, key):
    e = next(x for x in exchanges if x['i'] == item['exchange'])
    later = [x['user'][:400] for x in exchanges if x['i'] > item['exchange']][:25]
    state = {'candidate': {'type': item['type'], 'statement': mask(item['statement'])[0],
                           'quote': mask(item['quote'])[0], 'quote_speaker': item['speaker']},
             'cited_exchange': {'owner': e['user'][:4000], 'assistant': e['assistant'][:4000]},
             'later_owner_messages': later}
    body = dict(state=state, model=jev.DEFAULT_MODEL, questions={k: dict(type='noul', instructions=v) for k, v in VERIFY.items()})
    answers = jev.call(body, key)['answers']
    return {k: jev.probability(answers[k]['noul']) for k in VERIFY}


def compare(item, workspace, key):
    """Candidates come unjudged: recall's judge asks whether a passage answers a question, and a
    statement is not one. The same/contradicts/done questions below decide instead."""
    from dataclasses import replace
    from .recall import recall
    unjudged = replace(workspace, retrieval=replace(workspace.retrieval, reranker='off', vector_min_score=-1.0))
    extra = [item['quote'][:200]] + [k for k in (item.get('keywords') or []) if isinstance(k, str)][:3]
    found = recall(unjudged, item['statement'], queries=extra)
    evidence = [x for x in found.get('evidence', []) if 'chat-raw' not in x['path'] and '/harvest/' not in x['path']][:6]
    if not evidence:
        return 'new', None
    state = {'candidate': mask(item['statement'])[0],
             'passages': {f'p{i + 1}': {'source': x['path'], 'text': mask(x['text'])[0]} for i, x in enumerate(evidence)}}
    qs = {}
    for i in range(len(evidence)):
        qs[f'p{i + 1}_same'] = dict(type='noul', instructions=f"Does passage p{i + 1} already state the candidate's fact or decision (possibly in other words)?")
        qs[f'p{i + 1}_contra'] = dict(type='noul', instructions=f'Does passage p{i + 1} state something that contradicts the candidate (a different value, date or decision for the same thing)?')
        if item['type'] == 'open_item':
            qs[f'p{i + 1}_done'] = dict(type='noul', instructions=f"Does passage p{i + 1} report that the candidate's open item has since been done, resolved or dropped?")
    answers = jev.call(dict(state=state, model=jev.DEFAULT_MODEL, questions=qs), key)['answers']
    same = max((jev.probability(answers[f'p{i + 1}_same']['noul']), evidence[i]['path']) for i in range(len(evidence)))
    contra = max((jev.probability(answers[f'p{i + 1}_contra']['noul']), evidence[i]['path']) for i in range(len(evidence)))
    if item['type'] == 'open_item':
        done = max((jev.probability(answers[f'p{i + 1}_done']['noul']), evidence[i]['path']) for i in range(len(evidence)))
        if done[0] >= 0.5:
            return 'resolved', done[1]
    if same[0] >= 0.5:
        return 'known', same[1]
    if contra[0] >= 0.5:
        return 'conflict', contra[1]
    return 'new', None


def classify(item, scores):
    """Owner attribution decides whether a 'decision' is filed as the owner's."""
    if scores is None:
        return 'new'
    if scores['supported'] < 0.3 or scores['durable'] < 0.3:
        return 'drop'
    if scores['withdrawn'] >= 0.7:
        return 'review'
    if item['type'] in ('decision', 'preference') and scores['owner_stated'] < 0.5:
        item['attribution'] = 'assistant'
    return 'new'


# -- output --------------------------------------------------------------------

def _link(path, root):
    return '[[' + Path(path).stem + ']]'


def write_outputs(root, client, thread_id, exchanges, groups, meta, language, today=None):
    today = (today or datetime.date.today()).isoformat()
    short = thread_id.replace('-', '')[:8]
    raw_rel = f'sources/carry/harvest/{today} — {client} {short}.md'
    raw = Path(root) / raw_rel
    if not raw.exists():
        body = '\n\n'.join(f"## Exchange {e['i']}\n\n**Owner:** {e['user']}\n\n**Assistant:** {e['assistant']}" for e in exchanges)
        atomic_text(raw, f'---\ntype: chat-raw\nsummary: "{client} thread {short}, owner prompts and final replies, secrets masked"\n'
                         f'created: {today}\nsource_type: harvest\n---\n\n{body}\n')
    L = LABELS.get(language, LABELS['English'])
    counts = {k: len(v) for k, v in groups.items()}
    lines = ['---', 'type: output',
             f'summary: "{client} {short}: {counts.get("new", 0)} new, {counts.get("conflict", 0)} conflict, '
             f'{counts.get("known", 0)} already recorded"',
             f'created: {today}', 'draft: true', 'provenance: chat', 'sources:', f'  - "{_link(raw, root)}"',
             f'harvest: {json.dumps(meta, ensure_ascii=False)}', '---', '', f'# Harvest {client} {short}', '']
    def item_line(it):
        tag = L[it['type']] + (f' · {L["assistant"]}' if it.get('attribution') == 'assistant' else '')
        where = f' ↔ {_link(it["match"], root)}' if it.get('match') else ''
        return f'- **{tag}:** {it["statement"]}{where}\n  > {it["quote"]} *(exchange {it["exchange"]}, {it["speaker"]})*'
    for key in ('new', 'conflict', 'review'):
        if groups.get(key):
            lines += [f'## {L[key]}', ''] + [item_line(it) for it in groups[key]] + ['']
    for key in ('resolved', 'known'):
        if groups.get(key):
            lines += [f'## {L[key]}', ''] + [f'- {it["statement"]} → {_link(it["match"], root)}' for it in groups[key]] + ['']
    if meta.get('compared') is False:
        lines += [f'*{L["unchecked"]}*', '']
    digest_rel = f'+/{today} — harvest {client} {short}.md'
    atomic_text(Path(root) / digest_rel, '\n'.join(lines))
    return raw_rel, digest_rel


# -- run -----------------------------------------------------------------------

def _state(workspace):
    try:
        return json.loads((workspace.state_dir / STATE_NAME).read_text())
    except (OSError, ValueError):
        return {}


def vault_root(workspace, override=None):
    if override:
        return Path(override).expanduser().resolve()
    for s in workspace.sources:
        if s.source_id == 'vault':
            return Path(s.root).resolve()
    raise CarryError('harvest_needs_vault_source')


MIN_EXCHANGES = 2  # a one-question thread rarely holds a durable decision and costs a full extraction


def thread_from_path(path, root):
    """(client, thread id, path) for one transcript, or None when it is not a thread of this vault."""
    path = Path(path).expanduser().resolve()
    for client, thread_id, p in find_threads(root):
        if p.resolve() == path:
            return client, thread_id, p
    return None


def run(workspace, root=None, dry_run=False, include_filed=False, limit=None, min_idle=MIN_IDLE_MINUTES,
        language=None, which=None, progress=print, thread=None, min_exchanges=None):
    root = vault_root(workspace, root)
    min_exchanges = MIN_EXCHANGES if min_exchanges is None else min_exchanges
    if language is None:
        try:
            language = json.loads((root / '.carry' / 'vault.json').read_text()).get('language', 'English')
        except (OSError, ValueError):
            language = 'English'
    state = _state(workspace)
    key = jev.api_key()
    report = dict(threads=0, harvested=0, skipped_filed=0, skipped_active=0, pending=0, items=0, digests=[])
    if thread is not None:
        one = thread_from_path(thread, root)
        threads = [one] if one else []
        min_idle = 0  # the client just closed it
    else:
        threads = find_threads(root)
    for client, thread_id, path in threads:
        report['threads'] += 1
        entry_key = f'{client}:{thread_id}'
        mtime = path.stat().st_mtime
        previous = state.get(entry_key, {})
        if previous.get('mtime') == mtime and previous.get('status') in ('done', 'filed', 'empty'):
            continue
        if time.time() - mtime < min_idle * 60:
            report['skipped_active'] += 1
            continue
        exchanges, filed = read_thread(client, path, root)
        start = previous.get('exchanges_done', 0)
        fresh = [e for e in exchanges if e['i'] > start]
        if not fresh or len(exchanges) < min_exchanges:
            # Too short so far: keep the start so the whole thread is read once it grows.
            state[entry_key] = dict(mtime=mtime, status='empty', exchanges_done=start)
            continue
        if filed and not include_filed:
            report['skipped_filed'] += 1
            state[entry_key] = dict(mtime=mtime, status='filed', exchanges_done=len(exchanges))
            continue
        if limit is not None and report['harvested'] >= limit:
            break
        if dry_run:
            progress(f'would harvest {client} {thread_id[:8]}: {len(fresh)} exchanges')
            report['harvested'] += 1
            continue
        try:
            chosen = which or extractor()
            raw_items = []
            context = [e for e in exchanges if start - 2 < e['i'] <= start]  # neighbours for pronouns
            for window in _windows(context + fresh):
                raw_items += extract_window(window, language, chosen)
            items, dropped = provenance(raw_items, exchanges)
            groups = dict(new=[], conflict=[], review=[], resolved=[], known=[])
            for it in items:
                scores = verify(it, exchanges, key) if key else None
                verdict = classify(it, scores)
                if verdict == 'drop':
                    continue
                if verdict == 'new' and key:
                    verdict, it['match'] = compare(it, workspace, key)
                groups[verdict].append(it)
            meta = dict(extractor=chosen, judge='jev' if key else None, compared=bool(key),
                        dropped_without_quote=dropped, thread=entry_key, exchanges=[fresh[0]['i'], fresh[-1]['i']])
            raw_rel, digest_rel = write_outputs(root, client, thread_id, exchanges, groups, meta, language)
        except (CarryError, jev.JevUnavailable, subprocess.SubprocessError, OSError) as exc:
            report['pending'] += 1
            state[entry_key] = dict(previous, status='pending', error=str(exc))
            progress(f'pending {client} {thread_id[:8]}: {exc}')
            continue
        state[entry_key] = dict(mtime=mtime, status='done', exchanges_done=len(exchanges), digest=digest_rel)
        report['harvested'] += 1
        report['items'] += sum(len(v) for v in groups.values())
        report['digests'].append(digest_rel)
        progress(f'harvested {client} {thread_id[:8]}: ' + ', '.join(f'{k} {len(v)}' for k, v in groups.items()))
    if not dry_run:
        atomic_text(workspace.state_dir / STATE_NAME, json.dumps(state, indent=1))
    return report


def hook_payload(stream):
    try:
        payload = json.loads(stream.read() or '{}')
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def in_vault(cwd, root):
    try:
        cwd = Path(cwd).expanduser().resolve()
    except (OSError, TypeError):
        return False
    root = Path(root).resolve()
    return cwd == root or root in cwd.parents


def spawn_from_hook(state_dir, payload, root, language=None, python=None):
    """SessionEnd: start the harvest of the closed thread in the background and return at once
    (Codex allows a SessionEnd hook three seconds at most)."""
    import sys
    transcript = payload.get('transcript_path')
    if not transcript or not in_vault(payload.get('cwd') or '', root):
        return False
    args = [python or sys.executable, '-I', '-m', 'carry.cli', '--workspace', str(state_dir), 'harvest',
            '--thread', str(transcript), '--vault', str(root)] + (['--language', language] if language else [])
    log = open(Path(state_dir) / 'harvest.log', 'a')
    subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    return True


LAUNCH_LABEL = 'local.carry.harvest'


def schedule_plist(carry_bin, state_dir, hour=21, minute=30, extra=()):
    return f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LAUNCH_LABEL}</string>
  <key>ProgramArguments</key><array><string>{carry_bin}</string><string>--workspace</string><string>{state_dir}</string><string>harvest</string>{''.join(f'<string>{a}</string>' for a in extra)}</array>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>{hour}</integer><key>Minute</key><integer>{minute}</integer></dict>
  <key>StandardOutPath</key><string>{state_dir}/harvest.log</string>
  <key>StandardErrorPath</key><string>{state_dir}/harvest.log</string>
</dict></plist>
'''

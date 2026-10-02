"""Retrieval: hybrid search, evidence selection, honest degradation.

Every returned passage carries where it came from and what state it is in.
When a branch of the search is unavailable the result says so rather than
silently returning weaker evidence.
"""
import json
import math
import os
import re
import sqlite3
import time
from contextlib import closing

from .embedding import build_provider
from .errors import ProviderUnavailable
from .index import db_is_usable, fingerprint, health, open_readonly, read_manifest, unpack

MAX_QUERY_CHARS = 2000
BUDGET_LIMITS = {"top_k": (1, 20), "max_chars": (200, 20000), "max_per_document": (1, 10)}

METADATA_KEYS = ("type", "summary", "created", "date", "updated", "sources", "provenance",
                 "confidence", "sensitivity", "draft", "valid_until", "review_by")

try:  # optional acceleration
    import numpy as _np
except ImportError:  # pragma: no cover - exercised on installs without numpy
    _np = None


def normalize_budget(retrieval, budget=None):
    """Validate and cap a caller-supplied budget. A caller cannot raise limits
    above the workspace configuration, only lower them."""
    resolved = dict(top_k=retrieval.top_k, max_chars=retrieval.max_chars,
                    max_per_document=retrieval.max_per_document)
    for key, value in (budget or {}).items():
        if key not in resolved:
            raise ValueError("unknown_budget_key:" + str(key))
        if type(value) is not int or value <= 0:
            raise ValueError("invalid_budget_value:" + str(key))
        number = value
        _, high = BUDGET_LIMITS[key]
        resolved[key] = min(number, high, resolved[key])
    return resolved


PREFIX_MIN = 5  # words this long also match by a stem-like prefix: Turkish suffixes (kurulumu, kedim)


def fts_match_string(query, prefix=None):
    tokens = re.findall(r"\w{2,}", query, flags=re.UNICODE)
    if prefix is None:
        prefix = os.environ.get("CARRY_PREFIX_MATCH", "1") != "0"
    terms = [f'"{t}"' for t in tokens]
    if prefix:
        terms += [f'"{t[:max(4, len(t) - 3)]}"*' for t in dict.fromkeys(tokens) if len(t) >= PREFIX_MIN]
    return " OR ".join(terms) if terms else ""


def unmatched_terms(con, query, limit=12):
    """Query terms that appear nowhere in the corpus.

    A caller asking about something the corpus never mentions deserves to see
    that fact next to the passages, so a partial keyword match is not mistaken
    for an answer.
    """
    missing = []
    for token in dict.fromkeys(re.findall(r"\w{3,}", query.lower(), flags=re.UNICODE)):
        if len(missing) >= limit:
            break
        try:
            row = con.execute(
                "SELECT 1 FROM chunks_fts WHERE chunks_fts MATCH ? LIMIT 1",
                (f'"{token}"',)).fetchone()
        except sqlite3.Error:
            return missing
        if row is None:
            missing.append(token)
    return missing


def _cosine_ranking(rows, query_vector, limit, min_score=-1.0, scores_out=None):
    if _np is not None:
        matrix = _np.array([unpack(r["vec"], r["dim"]) for r in rows], dtype="float32")
        matrix /= _np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
        vector = _np.array(query_vector, dtype="float32")
        vector /= _np.linalg.norm(vector) + 1e-9
        scores = matrix @ vector
        order = _np.argsort(-scores)[:limit]
        if scores_out is not None:
            scores_out.update({rows[i]["id"]: float(scores[i]) for i in order})
        return [rows[i]["id"] for i in order if float(scores[i]) >= min_score]
    norm = math.sqrt(sum(v * v for v in query_vector)) or 1.0
    scored = []
    for row in rows:
        vec = unpack(row["vec"], row["dim"])
        length = math.sqrt(sum(v * v for v in vec)) or 1.0
        scored.append((sum(a * b for a, b in zip(vec, query_vector)) / (length * norm), row["id"]))
    scored.sort(key=lambda pair: -pair[0])
    if scores_out is not None:
        scores_out.update({cid: score for score, cid in scored[:limit]})
    return [cid for score, cid in scored[:limit] if score >= min_score]


def _candidates(workspace, query, con, limit, source_ids=None, diagnostics=None, eligible=None):
    """Independent lexical and vector branches fused with RRF."""
    diagnostics = diagnostics if diagnostics is not None else {}
    diagnostics.setdefault("warnings", [])
    retrieval = workspace.retrieval
    where, params = "", []
    if source_ids:
        where = " WHERE source_id IN (%s)" % ",".join("?" * len(source_ids))
        params = list(source_ids)
    rows = [dict(id=r[0], source_id=r[1], path=r[2], title=r[3], heading=r[4],
                 text=r[5], dim=r[6], vec=r[7])
            for r in con.execute(
                "SELECT id,source_id,path,title,heading,text,dim,vec FROM chunks" + where, params)]
    if eligible is not None:
        rows = [r for r in rows if (r['source_id'], r['path']) in eligible]
    if not rows:
        diagnostics["mode"] = "none"
        return []
    allowed = {r["id"] for r in rows}

    lexical = []
    match = fts_match_string(query)
    if match:
        try:
            lexical = [r[0] for r in con.execute(
                "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (match, -1))
                if r[0] in allowed][:retrieval.branch_candidates]
            diagnostics["unmatched_terms"] = unmatched_terms(con, query)
        except sqlite3.Error:
            diagnostics["warnings"].append("fts_unavailable")

    vector = []
    vector_scores = {}
    try:
        provider = build_provider(workspace.embedding)
        config, _ = read_manifest(con)
        if config.get("fingerprint") != fingerprint(workspace, provider):
            raise ProviderUnavailable("embedding_contract_mismatch")
        query_vector = provider.embed_query(query)
        if not query_vector or not all(math.isfinite(x) for x in query_vector):
            raise ProviderUnavailable("invalid_query_embedding")
        vector = _cosine_ranking(rows, query_vector, retrieval.branch_candidates,
                                 (-1.0 if retrieval.reranker in ("cross", "jev") else retrieval.vector_min_score) if provider.semantic else 0.01, vector_scores)
        if not provider.semantic:
            vector = [cid for cid in vector if cid in lexical]
        diagnostics["semantic"] = bool(provider.semantic)
    except Exception as exc:
        diagnostics["warnings"].append("vector_unavailable:" + type(exc).__name__)
        diagnostics["semantic"] = False

    diagnostics["answerability"] = "not_verified"
    diagnostics["relevance_gate"] = "vector_similarity_and_lexical_candidates"
    diagnostics["mode"] = ("hybrid" if lexical and vector else "vector" if vector
                           else "lexical" if lexical else "none")
    scores = {}
    for branch in (vector, lexical):
        for rank, chunk_id in enumerate(branch, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (retrieval.rrf_k + rank)
    ordered = sorted(scores, key=lambda cid: -scores[cid])[:limit]
    by_id = {r["id"]: r for r in rows}
    return [dict(by_id[cid], vector_score=vector_scores.get(cid)) for cid in ordered]


POOL_MAX = 32
MAX_EXTRA_QUERIES = 4


def is_secret(metadata):
    """Notes marked secret never go to an external judge."""
    return str((metadata or {}).get("sensitivity", "")).lower() == "secret"


def is_pending_digest(metadata):
    """A chat digest waits for review like a proposal until approval clears its draft flag,
    wherever it lands: a folder that does not exclude +/ must not serve it as an accepted note."""
    metadata = metadata or {}
    return (isinstance(metadata.get("harvest"), dict) and "extractor" in metadata["harvest"]
            and metadata.get("draft") in (True, "true"))


def _local_only(workspace, files, row):
    """Passages an external judge must never see: notes marked secret. With Jev chosen,
    every other passage may be sent; without it nothing is sent at all."""
    return is_secret((files or {}).get((row["source_id"], row["path"]), {}).get("metadata"))


def search(workspace, query, con, limit, source_ids=None, diagnostics=None, eligible=None, queries=(), files=None):
    """Candidates for the query, pooled with those of the caller's keyword variants, then judged
    once against the original question."""
    candidates = _candidates(workspace, query, con, limit, source_ids, diagnostics, eligible)
    extra = [q.strip()[:MAX_QUERY_CHARS] for q in queries if isinstance(q, str) and q.strip()][:MAX_EXTRA_QUERIES]
    if extra:
        seen = {row["id"] for row in candidates}
        for variant in extra:
            for row in _candidates(workspace, variant, con, limit, source_ids, {"warnings": []}, eligible):
                if row["id"] not in seen:
                    seen.add(row["id"])
                    candidates.append(row)
        candidates = candidates[:max(limit, POOL_MAX)]
        diagnostics["query_variants"] = len(extra)
    # Do not call a lexical overlap a verified answer; final grounding remains
    # the assistant's responsibility. A configured judge (cross encoder or Jev) adds a gate.
    from .rerank import rerank
    if workspace.retrieval.reranker == "jev":
        held = [r for r in candidates if _local_only(workspace, files, r)]
        if held:
            diagnostics["local_only_unjudged"] = len(held)
            sent = [r for r in candidates if not _local_only(workspace, files, r)]
            judged = rerank(query, sent, workspace.retrieval, diagnostics) if sent else []
            return judged + [dict(r, judged=False) for r in held]
    return rerank(query, candidates, workspace.retrieval, diagnostics)


def select_evidence(rows, budget):
    """Deduplicate, cap per document and respect the character budget."""
    selected, seen, per_document, size = [], set(), {}, 0
    for row in rows:
        key = (row["source_id"], row["path"])
        normalized = " ".join(row["text"].split())
        if normalized in seen or per_document.get(key, 0) >= budget["max_per_document"]:
            continue
        if size + len(row["text"]) > budget["max_chars"] and selected:
            continue
        if len(row["text"]) > budget["max_chars"] - size:
            row = dict(row, text=row["text"][:budget["max_chars"] - size], truncated=True)
        seen.add(normalized)
        per_document[key] = per_document.get(key, 0) + 1
        size += len(row["text"])
        selected.append(row)
        if len(selected) >= budget["top_k"]:
            break
    return selected


def _file_records(con, source_ids=None):
    where, params = "", []
    if source_ids:
        where = " WHERE source_id IN (%s)" % ",".join("?" * len(source_ids))
        params = list(source_ids)
    result = {}
    for row in con.execute(
            "SELECT source_id,path,metadata,record_id,revision,state,current,superseded_by,digest"
            " FROM files" + where, params):
        try:
            metadata = json.loads(row[2])
        except (TypeError, ValueError):
            metadata = {}
        result[(row[0], row[1])] = dict(
            metadata={k: metadata[k] for k in METADATA_KEYS if k in metadata},
            pending_digest=is_pending_digest(metadata),
            record_id=row[3], revision=row[4], state=row[5],
            current=bool(row[6]), superseded_by=row[7] or "", digest=row[8])
    return result


def _evidence_date(row, record):
    from .timeframe import row_date
    day = row.get("date") or row_date(row.get("heading"), row["path"], record.get("metadata"))
    return day.isoformat() if day else None


def citation(source_id, path, title):
    return f"{title} ({source_id}:{path})"


def _eligible(workspace, files, include_drafts=False, include_history=False, diagnostics=None):
    """Keys of indexed files recall may serve; annotates each record with its lifecycle state."""
    # Resolve accepted target links from canonical Markdown even if the
    # last build failed. Never serve old bytes as the current decision.
    from .lifecycle import catalog
    live = catalog(workspace)
    eligible = set()
    for key, record in files.items():
        current = live.get(key)
        if current is None or current['digest'] != record['digest']:
            continue
        record.update(state=current['state'], current=current['current'],
                      superseded_by=current['superseded_by'])
        if current.get('correction_conflict'):
            record['correction_conflict'] = current['correction_conflict']
            if diagnostics is not None:
                diagnostics['warnings'].append('correction_target_conflict')
        if current['state'] == 'rejected':
            continue
        if (current['state'] == 'draft' or record.get('pending_digest')) and not include_drafts:
            continue
        if not current['current'] and not include_history:
            continue
        eligible.add(key)
    return eligible, live


CATALOG_MAX_CHARS = 120000


def catalog_entries(workspace, source_ids=None, folder=None, max_chars=CATALOG_MAX_CHARS):
    """One line per servable file (path and summary, else title): the index an agent
    reads when keyword search cannot reach a note. Same eligibility as recall."""
    if not db_is_usable(workspace.db_path):
        return dict(ok=False, error="index_unavailable", entries=[])
    folder = (folder or "").strip().strip("/")
    with closing(open_readonly(workspace.db_path)) as con:
        eligible, _ = _eligible(workspace, _file_records(con, source_ids))
        first = {}
        for sid, path, title, heading, text in con.execute(
                "SELECT source_id, path, title, heading, text FROM chunks ORDER BY id"):
            key = (sid, path)
            if key not in eligible or (folder and not (path == folder or path.startswith(folder + "/"))):
                continue
            if heading == "summary":
                first[key] = text
            else:
                first.setdefault(key, title)
    entries, size, truncated = [], 0, False
    for (sid, path), line in sorted(first.items()):
        entry = dict(source_id=sid, path=path, summary=" ".join(line.split())[:300])
        size += len(path) + len(entry["summary"]) + 8
        if size > max_chars:
            truncated = True
            break
        entries.append(entry)
    return dict(ok=True, entries=entries, total=len(first), truncated=truncated)


LISTING_TEXT = 600  # per passage when a time range is listed, so more of the range fits the budget


def _dated_rows(con, eligible, files, frame):
    """Passages of eligible files that belong to the frame, with the date they belong to."""
    from .timeframe import row_date
    out = []
    for cid, sid, path, title, heading, text in con.execute(
            "SELECT id,source_id,path,title,heading,text FROM chunks ORDER BY id"):
        key = (sid, path)
        if key not in eligible:
            continue
        metadata = files.get(key, {}).get("metadata")
        day = row_date(heading, path, metadata, frame)
        if frame.contains(day):
            out.append(dict(id=cid, source_id=sid, path=path, title=title, heading=heading, text=text, date=day, metadata=metadata))
    return out


RAW_TYPES = ("chat-raw", "source", "clip")


def _written(row):
    """On the same day, a written-up note says what happened better than a raw chat log or a clip."""
    kind = str((row.get("metadata") or {}).get("type", "")).lower()
    return 0 if kind in RAW_TYPES or "chat-raw" in row["path"] else 1


def topic_words(residual):
    import re
    from .timeframe import GENERIC, _lower
    return [w for w in re.findall(r"\w+", _lower(residual)) if len(w) >= 3 and w not in GENERIC and not w.isdigit()]


def _topic_rows(rows, residual):
    """Rows about the question's topic words (matched by stem, for Turkish endings): a word in
    the title or heading counts most, then how often the text repeats it. Weakly related rows
    (less than half the best score) are left out."""
    from .timeframe import _lower
    words = {w[:5] for w in topic_words(residual)}
    if not words:
        return []
    scored = []
    for row in rows:
        head = _lower((row["title"] or "") + " " + (row["heading"] or "") + " " + row["path"])
        text = _lower(row["text"])
        score = sum(6 * (w in head) + min(text.count(w), 4) for w in words)
        scored.append((score, row))
    top = max((n for n, _ in scored), default=0)
    return [row for n, row in sorted(scored, key=lambda x: -x[0]) if top and n >= max(2, top / 2)]


def _listing(rows):
    """One passage per note for "what happened then": the note's own dated section inside
    the range, else its summary, else its first passage; newest first."""
    # A raw chat log or clip only speaks for a day that has no written-up note.
    written_days = {r["date"] for r in rows if _written(r)}
    rows = [r for r in rows if _written(r) or r["date"] not in written_days]
    best = {}
    rank = lambda r: (r["date"], r["heading"] and r["heading"][:4].isdigit(), r["heading"] == "summary")
    for row in rows:
        key = (row["source_id"], row["path"])
        if key not in best or rank(row) > rank(best[key]):
            best[key] = row
    ordered = sorted(best.values(), key=lambda r: (r["date"], _written(r), r["path"]), reverse=True)
    return [dict(r, text=r["text"] if len(r["text"]) <= LISTING_TEXT else r["text"][:LISTING_TEXT].rsplit(" ", 1)[0] + " …",
                 truncated=len(r["text"]) > LISTING_TEXT) for r in ordered], len(best)


def recall(workspace, query, source_ids=None, budget=None, include_history=False, include_drafts=False, queries=(), today=None):
    """Return cited evidence, or an explicit no-evidence / unavailable state."""
    started = time.monotonic()
    workspace.validate()
    query = (query or "").strip()[:MAX_QUERY_CHARS]
    known = {s.source_id for s in workspace.sources}
    if source_ids:
        source_ids = list(dict.fromkeys(source_ids))
        unknown = [s for s in source_ids if s not in known]
        if unknown:
            return dict(ok=False, status="invalid_request", error="unknown_source_ids",
                        detail=unknown, evidence=[], sources=[], diagnostics={})
    try:
        limits = normalize_budget(workspace.retrieval, budget)
    except ValueError as exc:
        return dict(ok=False, status="invalid_request", error=str(exc),
                    evidence=[], sources=[], diagnostics={})
    if not query:
        return dict(ok=False, status="invalid_request", error="empty_query",
                    evidence=[], sources=[], diagnostics={}, budget=limits)
    if not db_is_usable(workspace.db_path):
        return dict(ok=False, status="unavailable", error="index_unavailable",
                    evidence=[], sources=[], budget=limits,
                    diagnostics=dict(index=health(workspace)))

    diagnostics = {}
    try:
        with closing(open_readonly(workspace.db_path)) as con:
            index_health = health(workspace, con)
            files = _file_records(con, source_ids)
            diagnostics['warnings'] = []
            eligible, live = _eligible(workspace, files, include_drafts, include_history, diagnostics)
            diagnostics['pending_proposals'] = sum(f['state'] == 'draft' for f in live.values()
                if not source_ids or f['source_id'] in source_ids)
            from .timeframe import parse as parse_time
            frame = parse_time(query, today)
            if frame is None:
                candidates = search(workspace, query, con, limits['top_k'] * 3,
                                    source_ids=source_ids, diagnostics=diagnostics, eligible=eligible,
                                    queries=queries or (), files=files)
            else:
                # A named time: only passages dated inside it may answer.
                dated = _dated_rows(con, eligible, files, frame)
                in_range = {(r["source_id"], r["path"]) for r in dated}
                dated_ids = {r["id"]: r["date"] for r in dated}
                diagnostics["timeframe"] = dict(frame.to_json(), documents=len(in_range))
                candidates = []
                if not frame.activity:
                    # A topic with a time: the time may be when it happened rather than when the
                    # note was written ("why did the bill double in July?"). Passages dated inside
                    # come first; the rest of the vault still answers after them.
                    inside = []
                    if in_range:
                        found = search(workspace, frame.residual, con, limits['top_k'] * 3,
                                       source_ids=source_ids, diagnostics=diagnostics, eligible=in_range,
                                       queries=queries or (), files=files)
                        inside = [dict(r, date=dated_ids[r["id"]], in_range=True) for r in found if r["id"] in dated_ids]
                    outside_diag = {"warnings": []}
                    outside = search(workspace, query, con, limits['top_k'] * 3, source_ids=source_ids,
                                     diagnostics=outside_diag if inside else diagnostics, eligible=eligible,
                                     queries=queries or (), files=files)
                    seen = {r["id"] for r in inside}
                    candidates = inside + [dict(r, in_range=False) for r in outside if r["id"] not in seen]
                    diagnostics["timeframe"]["inside"] = len(inside)
                if not candidates and not frame.activity:
                    # Nothing judged relevant: list only the range's passages that share the
                    # question's words, never the whole range as if it answered a topic.
                    dated = _topic_rows(dated, frame.residual)
                    diagnostics["timeframe"]["topic_filtered"] = True
                    diagnostics["timeframe"]["topic"] = " ".join(topic_words(frame.residual))
                if not candidates and dated:
                    candidates, listed = _listing(dated)
                    diagnostics["timeframe"]["listing"] = True
                    # Count what the list can show: raw logs of days with a written note are left out.
                    diagnostics["timeframe"]["documents"] = listed
                    diagnostics["answerability"] = "date_range_listing"
                    diagnostics["relevance_gate"] = "date_range"
                    diagnostics.setdefault("mode", "date_range")
                    # Shorter passages, twice as many: a range is read as a list.
                    limits = dict(limits, top_k=limits['top_k'] * 2)
    except (OSError, sqlite3.Error, ValueError) as exc:
        return dict(ok=False, status="unavailable", error="index_read_failed:" + type(exc).__name__,
                    evidence=[], sources=[], budget=limits,
                    diagnostics=dict(index=health(workspace)))

    if not include_history:
        candidates = [r for r in candidates
                      if files.get((r["source_id"], r["path"]), {}).get("current", True)]
    hits = select_evidence(candidates, limits)

    from .github import citation_url
    evidence = []
    for row in hits:
        record = files.get((row["source_id"], row["path"]), {})
        source = workspace.source(row["source_id"])
        evidence.append(dict(
            scope=source.scope, repository=(source.github or {}).get("repository"),
            source_id=row["source_id"], path=row["path"], title=row["title"],
            heading=row["heading"], text=row["text"], truncated=row.get("truncated", False),
            citation=citation(row["source_id"], row["path"], row["title"]),
            url=citation_url(workspace.source(row["source_id"]), row["path"]),
            relevance_score=row.get("relevance_score"), vector_score=row.get("vector_score"),
            date=_evidence_date(row, record), in_range=row.get("in_range"),
            record_id=record.get("record_id", ""), revision=record.get("revision", 1),
            state=record.get("state", "imported"), current=record.get("current", True),
            superseded_by=record.get("superseded_by", ""),
            correction_conflict=record.get("correction_conflict"),
            metadata=record.get("metadata", {})))
    sources = list({(e["source_id"], e["path"]): dict(
        source_id=e["source_id"], path=e["path"], title=e["title"],
        citation=e["citation"], record_id=e["record_id"], revision=e["revision"],
        state=e["state"]) for e in evidence}.values())

    from .maintenance import sync_status
    remote_states = sync_status(workspace)
    diagnostics['github_sync'] = {s.source_id: dict(remote_states.get(s.source_id, {}), commit=s.github.get('commit'), synced_at=s.github.get('synced_at')) for s in workspace.sources if s.github and (not source_ids or s.source_id in source_ids)}
    if any(v.get('status') == 'failed' for v in diagnostics['github_sync'].values()):
        diagnostics['warnings'].append('github_sync_failed_using_last_successful_snapshot')
    diagnostics.update(index=index_health, budget=limits,
                       elapsed_ms=round((time.monotonic() - started) * 1000),
                       returned_chars=sum(len(e["text"]) for e in evidence),
                       candidates=len(candidates), scoped_to=source_ids or "all")
    diagnostics.setdefault("semantic", False)
    if not diagnostics["semantic"]:
        diagnostics["warnings"].append("results_are_lexical_only")
    if index_health.get("state") != "fresh":
        diagnostics["warnings"].append("index_" + str(index_health.get("state")))
    return dict(ok=True, status="evidence" if evidence else "no_evidence",
                evidence=evidence, sources=sources, diagnostics=diagnostics, budget=limits)

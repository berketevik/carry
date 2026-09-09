"""Retrieval: hybrid search, evidence selection, honest degradation.

Every returned passage carries where it came from and what state it is in.
When a branch of the search is unavailable the result says so rather than
silently returning weaker evidence.
"""
import json
import math
import re
import sqlite3
import time
from contextlib import closing

from .embedding import build_provider
from .errors import ProviderUnavailable
from .index import db_is_usable, fingerprint, health, open_readonly, read_manifest, unpack

MAX_QUERY_CHARS = 2000
BUDGET_LIMITS = {"top_k": (1, 20), "max_chars": (200, 20000), "max_per_document": (1, 10)}

METADATA_KEYS = ("type", "summary", "created", "updated", "sources", "provenance",
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
        try:
            number = int(value)
        except (TypeError, ValueError):
            raise ValueError("invalid_budget_value:" + str(key))
        low, high = BUDGET_LIMITS[key]
        resolved[key] = max(low, min(number, high, resolved[key]))
    return resolved


def fts_match_string(query):
    tokens = re.findall(r"\w{2,}", query, flags=re.UNICODE)
    return " OR ".join(f'"{t}"' for t in tokens) if tokens else ""


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


def _cosine_ranking(rows, query_vector, limit):
    if _np is not None:
        matrix = _np.array([unpack(r["vec"], r["dim"]) for r in rows], dtype="float32")
        matrix /= _np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-9
        vector = _np.array(query_vector, dtype="float32")
        vector /= _np.linalg.norm(vector) + 1e-9
        scores = matrix @ vector
        order = _np.argsort(-scores)[:limit]
        return [rows[i]["id"] for i in order]
    norm = math.sqrt(sum(v * v for v in query_vector)) or 1.0
    scored = []
    for row in rows:
        vec = unpack(row["vec"], row["dim"])
        length = math.sqrt(sum(v * v for v in vec)) or 1.0
        scored.append((sum(a * b for a, b in zip(vec, query_vector)) / (length * norm), row["id"]))
    scored.sort(key=lambda pair: -pair[0])
    return [cid for _, cid in scored[:limit]]


def search(workspace, query, con, limit, source_ids=None, diagnostics=None):
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
                (match, retrieval.branch_candidates * (3 if source_ids else 1)))
                if r[0] in allowed]
            diagnostics["unmatched_terms"] = unmatched_terms(con, query)
        except sqlite3.Error:
            diagnostics["warnings"].append("fts_unavailable")

    vector = []
    try:
        provider = build_provider(workspace.embedding)
        config, _ = read_manifest(con)
        if config.get("fingerprint") != fingerprint(workspace, provider):
            raise ProviderUnavailable("embedding_contract_mismatch")
        query_vector = provider.embed_query(query)
        if not query_vector or not all(math.isfinite(x) for x in query_vector):
            raise ProviderUnavailable("invalid_query_embedding")
        vector = _cosine_ranking(rows, query_vector, retrieval.branch_candidates)
        diagnostics["semantic"] = bool(provider.semantic)
    except Exception as exc:
        diagnostics["warnings"].append("vector_unavailable:" + type(exc).__name__)
        diagnostics["semantic"] = False

    diagnostics["mode"] = ("hybrid" if lexical and vector else "vector" if vector
                           else "lexical" if lexical else "none")
    scores = {}
    for branch in (vector, lexical):
        for rank, chunk_id in enumerate(branch, 1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (retrieval.rrf_k + rank)
    ordered = sorted(scores, key=lambda cid: -scores[cid])[:limit]
    by_id = {r["id"]: r for r in rows}
    return [by_id[cid] for cid in ordered]


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
            "SELECT source_id,path,metadata,record_id,revision,state,current,superseded_by"
            " FROM files" + where, params):
        try:
            metadata = json.loads(row[2])
        except (TypeError, ValueError):
            metadata = {}
        result[(row[0], row[1])] = dict(
            metadata={k: metadata[k] for k in METADATA_KEYS if k in metadata},
            record_id=row[3], revision=row[4], state=row[5],
            current=bool(row[6]), superseded_by=row[7] or "")
    return result


def citation(source_id, path, title):
    return f"{title} ({source_id}:{path})"


def recall(workspace, query, source_ids=None, budget=None, include_history=False):
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
            candidates = search(workspace, query, con, limits["top_k"] * 3,
                                source_ids=source_ids, diagnostics=diagnostics)
            files = _file_records(con, source_ids)
    except (OSError, sqlite3.Error, ValueError) as exc:
        return dict(ok=False, status="unavailable", error="index_read_failed:" + type(exc).__name__,
                    evidence=[], sources=[], budget=limits,
                    diagnostics=dict(index=health(workspace)))

    if not include_history:
        candidates = [r for r in candidates
                      if files.get((r["source_id"], r["path"]), {}).get("current", True)]
    hits = select_evidence(candidates, limits)

    evidence = []
    for row in hits:
        record = files.get((row["source_id"], row["path"]), {})
        evidence.append(dict(
            source_id=row["source_id"], path=row["path"], title=row["title"],
            heading=row["heading"], text=row["text"],
            citation=citation(row["source_id"], row["path"], row["title"]),
            record_id=record.get("record_id", ""), revision=record.get("revision", 1),
            state=record.get("state", "imported"), current=record.get("current", True),
            superseded_by=record.get("superseded_by", ""),
            metadata=record.get("metadata", {})))
    sources = list({(e["source_id"], e["path"]): dict(
        source_id=e["source_id"], path=e["path"], title=e["title"],
        citation=e["citation"], record_id=e["record_id"], revision=e["revision"],
        state=e["state"]) for e in evidence}.values())

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

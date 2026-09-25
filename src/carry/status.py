"""Integration status: what is connected, what is fresh, what is degraded.

The payload is deliberately content-free. It reports counts, states and provider
names, never note text, query text or credentials.
"""
from .capture import capture_status
from .lifecycle import catalog
from .embedding import build_provider
from .errors import ProviderUnavailable
from .index import db_is_usable, health
from . import sidecar as sidecar_map


def status(workspace, probe_provider=False):
    workspace.validate()
    index_health = health(workspace)
    try:
        provider = build_provider(workspace.embedding)
        provider_info = dict(name=provider.name, semantic=provider.semantic, configured=True, model=workspace.embedding.model)
        if probe_provider:
            available, detail = provider.probe()
            provider_info.update(available=available, detail=detail)
    except ProviderUnavailable as exc:
        provider_info = dict(name=workspace.embedding.provider, semantic=False,
                             configured=False, available=False, detail=str(exc))
    sidecar = sidecar_map.load(workspace.sidecar_path)
    degradation = []
    if not provider_info.get("semantic"):
        degradation.append("lexical_only_provider")
    if index_health["state"] != "fresh":
        degradation.append("index_" + index_health["state"])
    if sidecar.get("reconcile"):
        degradation.append("records_need_reconciliation")
    current_records = catalog(workspace)
    review_info = dict(pending=sum(r['state'] == 'draft' for r in current_records.values()),
                       rejected=sum(r['state'] == 'rejected' for r in current_records.values()),
                       target_conflicts=sum(bool(r.get('correction_conflict')) for r in current_records.values()))
    if review_info['target_conflicts']:
        degradation.append('correction_target_conflict')
    from .maintenance import job_status, sync_status
    return dict(
        maintenance=job_status(workspace), github_sync=sync_status(workspace),
        review=review_info,
        workspace=dict(state_dir=str(workspace.state_dir),
                       sources=[dict(source_id=s.source_id, scope=s.scope,
                                     writable=s.writable, root=str(s.root), github=s.github, exclude=list(s.exclude),
                                     root_available=s.root.expanduser().exists())
                                for s in workspace.sources]),
        index=dict(usable=db_is_usable(workspace.db_path), **index_health),
        embedding=provider_info,
        records=dict(tracked=len(sidecar.get("entries", {})),
                     needs_reconciliation=len(sidecar.get("reconcile", []))),
        capture=capture_status(workspace),
        degradation=degradation,
        ok=index_health["state"] != "unavailable")

"""Search settings every device of a vault shares: `<vault>/.carry/settings.json`, committed.

A vault's Carry files live in its `.carry/` folder. `settings.json` holds what must be the
same wherever the vault is searched (the drafts' language, which folders stay out of search,
the ranking settings, the embedding model); `state/` holds everything that belongs to one
device. A workspace reads the shared file over its own `workspace.json` on every load, and
writes a changed shared value back to it, so one device's change reaches the others through
the vault's own sync. Device values (paths, the embedding provider and endpoint, timeouts)
never go in it.

The file travels between machines and people: unknown keys and bad values are reported and
skipped, never fatal.
"""
import json
from pathlib import Path

CARRY_DIR = '.carry'
SETTINGS_NAME = 'settings.json'
STATE_NAME = 'state'
VERSION = 1
MAX_BYTES = 64_000

# What a search returns and how notes become passages: the same on every device.
RETRIEVAL_KEYS = {
    'chunk_chars': int, 'chunk_overlap': int, 'branch_candidates': int, 'rrf_k': int, 'top_k': int,
    'max_chars': int, 'max_per_document': int, 'reranker': str, 'reranker_model': str,
    'reranker_min_score': float, 'vector_min_score': float,
}
# The model and its options decide the vectors; the provider (Ollama or the model built
# into Carry) and its endpoint are this device's choice.
EMBEDDING_KEYS = {'model': str, 'prefixes': bool, 'dim': int}
LANGUAGES = ('Turkish', 'English')
TOP_KEYS = ('version', 'language', 'exclude', 'retrieval', 'embedding')


def in_vault_root(state_dir):
    """The vault whose `.carry/state` this state directory is, else None."""
    state = Path(state_dir).expanduser().resolve()
    if state.name == STATE_NAME and state.parent.name == CARRY_DIR:
        return state.parent.parent
    return None


def home_source(workspace):
    """The source whose `.carry/` holds the shared settings: the vault the state lives in,
    else the one named `home` in workspace.json, else a source with the id `vault`."""
    root = in_vault_root(workspace.state_dir)
    for src in workspace.sources:
        try:
            resolved = Path(src.root).expanduser().resolve()
        except OSError:
            continue
        if root is not None and resolved == root:
            return src
    for wanted in (getattr(workspace, 'home', '') or None, 'vault'):
        for src in workspace.sources:
            if wanted and src.source_id == wanted and src.scope == 'personal':
                return src
    return None


def settings_path(workspace):
    src = home_source(workspace)
    if src is not None:
        return Path(src.root).expanduser().resolve() / CARRY_DIR / SETTINGS_NAME
    root = in_vault_root(workspace.state_dir)
    return root / CARRY_DIR / SETTINGS_NAME if root is not None else None


def _kind_ok(kind, value):
    if kind is bool:
        return isinstance(value, bool)
    if isinstance(value, bool):
        return False
    if kind is int:
        return isinstance(value, int)
    if kind is float:
        return isinstance(value, (int, float))
    return isinstance(value, str) and bool(value)


def parse(data):
    """(settings, warnings): only known keys with values of the right kind."""
    if not isinstance(data, dict):
        return {}, ['shared_settings_unreadable']
    warnings = [f'shared_settings_unknown_key:{k}' for k in data if k not in TOP_KEYS]
    out = {}
    if 'language' in data:
        if data['language'] in LANGUAGES:
            out['language'] = data['language']
        else:
            warnings.append('shared_settings_invalid:language')
    if 'exclude' in data:
        if isinstance(data['exclude'], list) and all(isinstance(p, str) for p in data['exclude']):
            out['exclude'] = list(data['exclude'])
        else:
            warnings.append('shared_settings_invalid:exclude')
    for section, keys in (('retrieval', RETRIEVAL_KEYS), ('embedding', EMBEDDING_KEYS)):
        raw = data.get(section)
        if raw is None:
            continue
        if not isinstance(raw, dict):
            warnings.append(f'shared_settings_invalid:{section}')
            continue
        good = {}
        for key, value in raw.items():
            kind = keys.get(key)
            if kind is None:
                warnings.append(f'shared_settings_unknown_key:{section}.{key}')
            elif not _kind_ok(kind, value):
                warnings.append(f'shared_settings_invalid:{section}.{key}')
            else:
                good[key] = float(value) if kind is float else value
        if good:
            out[section] = good
    return out, warnings


def read(path):
    """(settings, warnings) from a settings.json; ({}, []) when it is absent."""
    if path is None:
        return {}, []
    path = Path(path)
    try:
        if not path.is_file():
            return {}, []
        if path.stat().st_size > MAX_BYTES:
            return {}, ['shared_settings_too_large']
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {}, ['shared_settings_unreadable']
    return parse(data)


def values(workspace, language=None):
    """The shared values a workspace runs with now, in settings.json form."""
    out = dict(version=VERSION)
    if language:
        out['language'] = language
    src = home_source(workspace)
    if src is not None:
        out['exclude'] = list(src.exclude)
    out['retrieval'] = {k: getattr(workspace.retrieval, k) for k in RETRIEVAL_KEYS}
    out['embedding'] = {k: getattr(workspace.embedding, k) for k in EMBEDDING_KEYS}
    return out


def overlay(workspace, settings):
    """The workspace with the shared values in place of its own."""
    from dataclasses import replace
    if not settings:
        return workspace
    changes = {}
    if settings.get('retrieval'):
        changes['retrieval'] = replace(workspace.retrieval, **settings['retrieval'])
    if settings.get('embedding'):
        changes['embedding'] = replace(workspace.embedding, **settings['embedding'])
    if 'exclude' in settings:
        src = home_source(workspace)
        if src is not None:
            changes['sources'] = tuple(replace(s, exclude=tuple(settings['exclude'])) if s is src else s
                                       for s in workspace.sources)
    return replace(workspace, **changes) if changes else workspace


def differences(device, settings):
    """{key: {shared, device}} for shared keys where this device's own workspace.json differs."""
    out = {}
    for section in ('retrieval', 'embedding'):
        for key, shared in (settings.get(section) or {}).items():
            mine = (device.get(section) or {}).get(key)
            if mine is not None and mine != shared:
                out[f'{section}.{key}'] = dict(shared=shared, device=mine)
    if 'exclude' in settings and device.get('exclude') is not None and list(device['exclude']) != settings['exclude']:
        out['exclude'] = dict(shared=settings['exclude'], device=list(device['exclude']))
    return out


def render(settings):
    ordered = {k: settings[k] for k in TOP_KEYS if k in settings}
    return json.dumps(ordered, indent=2, ensure_ascii=False) + '\n'


def write(path, settings):
    """Atomic write; returns True when the file changed."""
    from .persistence import atomic_text
    path = Path(path)
    text = render(settings)
    try:
        if path.read_text(encoding='utf-8') == text:
            return False
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_text(path, text)
    return True


def write_back(workspace):
    """After a save: put the workspace's shared values into settings.json, keeping its language.
    Only where the standard layout applies: the state lives in the vault, or a home vault is
    named; a workspace kept elsewhere without one writes nothing."""
    path = settings_path(workspace)
    if path is None:
        return False
    if in_vault_root(workspace.state_dir) is None and not path.is_file():
        return False
    current, _ = read(path)
    merged = values(workspace, language=current.get('language'))
    if not current.get('language'):
        merged.pop('language', None)
    if {k: v for k, v in merged.items() if k != 'version'} == {k: v for k, v in current.items() if k != 'version'}:
        return False
    return write(path, merged)


def language(workspace, root=None):
    """The drafts' language from the shared settings, else None."""
    path = settings_path(workspace)
    if path is None and root is not None:
        path = Path(root) / CARRY_DIR / SETTINGS_NAME
    settings, _ = read(path)
    return settings.get('language')


def set_language(workspace, value):
    path = settings_path(workspace)
    if path is None or value not in LANGUAGES:
        return False
    if in_vault_root(workspace.state_dir) is None and not path.is_file():
        return False
    current, _ = read(path)
    if not current:
        current = values(workspace)
    current['language'] = value
    current['version'] = VERSION
    return write(path, current)

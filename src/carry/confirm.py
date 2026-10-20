"""Ask the owner before Carry searches the notes or saves to them, in Claude Code's own dialog.

Two of Claude Code's mechanisms, because each covers what the other cannot:

- The tools that read or write notes declare `_meta["anthropic/requiresUserInteraction"]`, so
  every call opens the approval dialog, in every permission mode and with no "don't ask again"
  (Claude Code 2.1.214+). It reaches every connected folder as soon as the server updates.
- `permissions.ask` rules in a connected folder's `.claude/settings.local.json`: the same tools
  for older clients, and the assistant's own edits of Markdown files there (file tools have no
  annotation).

On by default; `carry confirm off` turns both off on this device. Codex has no equivalent Carry
can set. Carry's own background writes (session recaps in `+/`, meeting notes) never run inside
a chat: they are drafts the owner decides in the app.
"""
import json
from pathlib import Path

from .errors import CarryError
from .persistence import atomic_text

NAME = 'confirm.json'
TOOLS = ('carry_recall', 'carry_catalog', 'carry_propose')
META = {'anthropic/requiresUserInteraction': True}
# Edit rules cover every file tool (Write and MultiEdit included); `/` is the folder the rules sit in.
RULES = tuple(f'mcp__carry__{name}' for name in TOOLS) + ('Edit(/*.md)', 'Edit(/**/*.md)')
SETTINGS = Path('.claude') / 'settings.local.json'


def enabled(state_dir):
    """Whether this device asks; on unless `carry confirm off` was run."""
    try:
        value = json.loads((Path(state_dir) / NAME).read_text(encoding='utf-8')).get('enabled')
    except (OSError, ValueError, AttributeError):
        return True
    return value is not False


def marked(tools, on):
    """The tool list with the reading and writing tools marked to ask on every call."""
    if not on:
        return tools
    return [dict(t, _meta=dict(META)) if t['name'] in TOOLS else t for t in tools]


def with_rules(data, on):
    """(settings, changed): Claude Code settings with Carry's ask rules added, or removed when off.
    Rules of the owner's own are kept either way."""
    permissions = data.get('permissions')
    if permissions is None and not on:
        return data, False
    if permissions is None:
        permissions = data['permissions'] = {}
    if not isinstance(permissions, dict) or not isinstance(permissions.get('ask', []), list):
        raise CarryError('invalid_permission_settings')
    ask = permissions.get('ask', [])
    if on:
        missing = [rule for rule in RULES if rule not in ask]
        if missing:
            permissions['ask'] = ask + missing
        return data, bool(missing)
    kept = [rule for rule in ask if rule not in RULES]
    if len(kept) == len(ask):
        return data, False
    if kept:
        permissions['ask'] = kept
    else:
        permissions.pop('ask', None)
        if not permissions:
            data.pop('permissions')
    return data, True


def folders(workspace):
    """Local folders whose `.mcp.json` starts Carry's server: the owner's vault, a team clone,
    and any project `carry connect` wired."""
    from . import connections
    roots = {Path(s.root).expanduser().resolve() for s in workspace.sources if not s.github}
    try:
        roots |= {Path(h['project']) for h in connections.history(workspace) if h.get('state') == 'applied'}
    except (CarryError, OSError, ValueError, KeyError):
        pass
    found = []
    for root in sorted(roots):
        try:
            servers = json.loads((root / '.mcp.json').read_text(encoding='utf-8')).get('mcpServers')
        except (OSError, ValueError, AttributeError):
            continue
        carry = servers.get('carry') if isinstance(servers, dict) else None
        if isinstance(carry, dict) and 'carry.mcp_server' in json.dumps(carry.get('args')):
            found.append(root)
    return found


def apply(workspace, on):
    """Turn asking on or off on this device: the switch the server reads, and Carry's ask rules in
    every connected folder. Returns the settings files that changed."""
    atomic_text(Path(workspace.state_dir) / NAME, json.dumps(dict(enabled=bool(on))) + '\n')
    changed, skipped = [], []
    for root in folders(workspace):
        path = root / SETTINGS
        if path.is_symlink():
            skipped.append(str(path))
            continue
        try:
            data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            if not isinstance(data, dict):
                raise ValueError(path)
            data, did = with_rules(data, on)
        except (OSError, ValueError, CarryError):
            skipped.append(str(path))
            continue
        if did:
            atomic_text(path, json.dumps(data, ensure_ascii=False, indent=2) + '\n')
            changed.append(str(path))
    return dict(enabled=bool(on), changed=changed, skipped=skipped)

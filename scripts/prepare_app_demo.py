"""Create an explicit synthetic workspace for native app checks; never overwrite."""
import argparse
import json
from pathlib import Path

from carry.config import Workspace, SourceConfig, EmbeddingConfig
from carry.fixtures import install_fixture
from carry import index, lifecycle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    base = args.destination.resolve()
    base.mkdir(parents=True, exist_ok=False)
    (base / 'records').mkdir()
    (base / 'project').mkdir()
    install_fixture(base / 'notes', include_readme=False)
    ws = Workspace.create(base / 'state', sources=[SourceConfig('notes', base / 'notes'),
        SourceConfig('records', base / 'records', True)], embedding=EmbeddingConfig(provider='hashing'))
    index.build(ws)
    old = lifecycle.propose(ws, 'Cedar pilot uses feature flags.', 'desktop-first', title='Cedar rollout safeguard')
    review = lifecycle.review(ws, old['record_id'])
    lifecycle.accept(ws, old['record_id'], review['revision'], review['review_token'])
    target = next(r for r in lifecycle.catalog(ws).values() if r['path'].endswith('Cedar Pilot Plan.md'))
    lifecycle.propose(ws, 'Cedar pilot delivery is October 22, 2026.', 'desktop-correction',
        title='Move Cedar delivery to October 22', target_id=target['record_id'], target_source_id='notes',
        expected_revision=target['revision'], source_refs=['notes:' + target['path']])
    print(json.dumps(dict(workspace=str(ws.state_dir), project=str(base / 'project'))))


if __name__ == '__main__':
    main()

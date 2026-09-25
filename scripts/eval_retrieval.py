"""Fixed synthetic EN/TR evidence evaluation. No private source data is needed."""
import argparse
import json
from pathlib import Path
import statistics
import tempfile
import time

from carry.config import Workspace, SourceConfig, EmbeddingConfig, RetrievalConfig
from carry.index import build
from carry.recall import recall


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--model', default='embeddinggemma')
    p.add_argument('--reranker', default='off', choices=('off', 'cross'))
    p.add_argument('--rerank-threshold', type=float, default=-4.0)
    p.add_argument('--threshold', type=float, default=0.45)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--split', choices=('development', 'held_out', 'all'), default='all')
    args = p.parse_args()
    corpus = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/retrieval.json').read_text())
    with tempfile.TemporaryDirectory(prefix='carry-eval-') as temp:
        root = Path(temp) / 'notes'; root.mkdir()
        for name, content in corpus['documents'].items():
            (root / name).write_text(content)
        config = EmbeddingConfig(provider='hashing' if args.model == 'hashing' else 'ollama',
                                 model=args.model, timeout=60)
        ws = Workspace.create(Path(temp) / 'state', sources=[SourceConfig('test', root)],
            embedding=config, retrieval=RetrievalConfig(top_k=3, max_per_document=1,
                reranker=args.reranker, reranker_min_score=args.rerank_threshold, vector_min_score=args.threshold, auto_refresh=False))
        built = build(ws)
        if built['status'] != 'built':
            raise RuntimeError(built)
        results = []
        for case in corpus['queries']:
            if args.split != 'all' and args.split != case['split']:
                continue
            t = time.monotonic(); result = recall(ws, case['query'])
            paths = [e['path'] for e in result['evidence']]
            expected = case['expected']
            results.append(dict(case, returned=paths, hit1=paths[:1] == [expected] if expected else None,
                hit3=expected in paths if expected else None, abstained=not paths,
                scores=[e.get('vector_score') for e in result['evidence']], rerank_scores=[e.get('relevance_score') for e in result['evidence']],
                ms=round((time.monotonic()-t)*1000), diagnostics=result['diagnostics']))
        metrics = {}
        for split in ('development', 'held_out'):
            rows = [r for r in results if r['split'] == split]
            positive = [r for r in rows if r['expected']]
            negative = [r for r in rows if not r['expected']]
            if rows:
                metrics[split] = dict(positive=len(positive), hit1=sum(r['hit1'] for r in positive),
                    hit3=sum(r['hit3'] for r in positive), negative=len(negative),
                    abstained=sum(r['abstained'] for r in negative), median_ms=statistics.median(r['ms'] for r in rows))
        report = dict(model=args.model, reranker=args.reranker, threshold=args.threshold,
                      scope='synthetic evidence retrieval, not generated answer accuracy', metrics=metrics, results=results)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2))
        print(json.dumps(dict(model=args.model, reranker=args.reranker, metrics=metrics)))

if __name__ == '__main__':
    main()

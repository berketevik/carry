"""Optional local cross-encoder; raw-logit relevance gate, never implicit downloads."""
from functools import lru_cache
import math


@lru_cache(maxsize=1)
def encoder(model):
    import torch
    from sentence_transformers import CrossEncoder
    # Apple silicon uses unified memory. FP32 plus multi-passage batches can
    # exhaust an 8 GB Mac when the assistant and embedding model are also live.
    if torch.backends.mps.is_available():
        torch.set_num_threads(min(torch.get_num_threads(), 2))
        result = CrossEncoder(model, local_files_only=True, max_length=1024,
                              device='mps', model_kwargs={'torch_dtype': torch.float16})
        result._carry_batch_size = 1
        return result
    return CrossEncoder(model, local_files_only=True, max_length=1024)


def rerank(query, rows, config, diagnostics):
    diagnostics['reranker'] = config.reranker
    if config.reranker != 'cross' or not rows:
        return rows
    try:
        model = encoder(config.reranker_model)
        scores = model.predict(
            [(query, row['text']) for row in rows], activation_fn=lambda logits: logits,
            show_progress_bar=False, batch_size=getattr(model, '_carry_batch_size', 4))
        if len(scores) != len(rows) or not all(math.isfinite(float(s)) for s in scores):
            raise ValueError('invalid_reranker_scores')
        ranked = sorted(zip(scores, rows), key=lambda pair: -float(pair[0]))
        diagnostics['relevance_gate'] = 'cross_encoder_raw_logit'
        diagnostics['relevance_threshold'] = config.reranker_min_score
        diagnostics['rejected_candidates'] = sum(float(score) <= config.reranker_min_score for score, _ in ranked)
        return [dict(row, relevance_score=float(score)) for score, row in ranked
                if float(score) > config.reranker_min_score]
    except Exception as exc:
        diagnostics['reranker'] = 'unavailable'
        diagnostics.setdefault('warnings', []).append('reranker_unavailable:' + type(exc).__name__)
        diagnostics['answerability'] = 'not_verified'
        return rows

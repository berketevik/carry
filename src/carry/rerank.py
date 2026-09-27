"""Optional local cross-encoder; raw-logit relevance gate, never implicit downloads."""
import math
import os
import threading

# Every client session runs its own MCP process, and a loaded reranker holds
# about 1.9 GB of unified memory. Release it after this many idle seconds; the
# next recall reloads it (a cold start of roughly 20 s). 0 keeps it loaded.
IDLE_SECONDS = float(os.environ.get('CARRY_RERANKER_IDLE_SECONDS', '600'))

_lock = threading.RLock()
_loaded = {}
_timer = None


def _load(model):
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


def encoder(model):
    with _lock:
        if model not in _loaded:
            _loaded.clear()
            _loaded[model] = _load(model)
        return _loaded[model]


def release():
    """Drop the loaded model and hand its memory back; returns whether one was loaded."""
    with _lock:
        if not _loaded:
            return False
        _loaded.clear()
    import gc
    gc.collect()
    try:
        import torch
        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
    except ImportError:
        pass
    return True


def _schedule_release():
    global _timer
    if IDLE_SECONDS <= 0:
        return
    with _lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(IDLE_SECONDS, release)
        _timer.daemon = True
        _timer.start()


def rerank(query, rows, config, diagnostics):
    diagnostics['reranker'] = config.reranker
    if config.reranker == 'jev' and rows:
        from . import jev
        return jev.rerank(query, rows, config, diagnostics)
    if config.reranker != 'cross' or not rows:
        return rows
    try:
        # The lock keeps an idle release from dropping the model mid-prediction.
        with _lock:
            model = encoder(config.reranker_model)
            scores = model.predict(
                [(query, row['text']) for row in rows], activation_fn=lambda logits: logits,
                show_progress_bar=False, batch_size=getattr(model, '_carry_batch_size', 4))
        _schedule_release()
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

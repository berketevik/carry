"""Explicit model installation with streamed progress and verified activation."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from dataclasses import replace
import urllib.request
from urllib.parse import urlsplit

from .background import detached, which
from .config import EmbeddingConfig, Workspace
from .embedding import build_provider
from .errors import CarryError
from .persistence import writer_lock

PRESETS = {
    'embeddinggemma': dict(prefixes=True),
    'qwen3-embedding:0.6b': dict(prefixes=True),
    'nomic-embed-text': dict(prefixes=True),
}


def ollama_binary():
    for candidate in (Path(sys.prefix).parent / 'ollama/ollama',
                      Path('/Applications/Ollama.app/Contents/Resources/ollama')):
        if candidate.is_file():
            return str(candidate)
    return which('ollama')


def ensure_runtime(endpoint):
    parsed = urlsplit(endpoint)
    if parsed.scheme != 'http' or parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
        raise CarryError('model_setup_requires_local_ollama')
    try:
        with urllib.request.urlopen(endpoint + '/api/version', timeout=2) as response:
            json.load(response)
        return
    except Exception:
        pass
    executable = ollama_binary()
    if not executable:
        raise CarryError('ollama_runtime_missing')
    # Only called by an explicit installation action; do not change an existing server.
    proc = detached([executable, 'serve'], env=dict(os.environ, OLLAMA_HOST=parsed.netloc),
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        try:
            with urllib.request.urlopen(endpoint + '/api/version', timeout=1) as response:
                json.load(response)
            return
        except Exception:
            if proc.poll() is not None:
                break
            time.sleep(0.25)
    raise CarryError('ollama_start_failed')


# The assistant's own small model (a carry-recall subagent) judges relevance, so no
# local relevance model is loaded: it needs 2-3 GB and stalled 8 GB Macs on the first
# recall. Recall hands over more, unfiltered candidates for that judge.
ASSISTANT_RANKED = dict(reranker='off', top_k=12, max_chars=16000, vector_min_score=-1.0)


# Nothing runs on this Mac beyond SQLite full-text search (about 50 MB, index in
# seconds): the carry-recall subagent searches with several keyword queries and
# judges the passages. Needs no Ollama and no download.
KEYWORD_ASSISTANT = dict(reranker='off', top_k=8, max_chars=10000, vector_min_score=-1.0)


# keyword_assistant plus TypeSafe Jev judging every recall in about half a second:
# no model on this Mac and no small-model subagent turn. Needs a TypeSafe API key
# (Keychain or TYPESAFE_API_KEY); without it recall returns unjudged candidates.
KEYWORD_JEV = dict(KEYWORD_ASSISTANT, reranker='jev', reranker_min_score=0.5)


# Measured best on the 45-case evaluation (hit@1 34/36, MRR 0.958): multilingual
# embeddinggemma through Ollama (~0.7 GB while loaded, unloads when idle) plus Jev.
SEMANTIC_JEV = dict(reranker='jev', reranker_min_score=0.5, top_k=8, max_chars=10000, vector_min_score=-1.0)
SEMANTIC_ASSISTANT = ASSISTANT_RANKED
SEARCH_PRESETS = {(False, 'assistant'): 'keyword_assistant', (False, 'jev'): 'keyword_jev',
                  (True, 'assistant'): 'semantic_assistant', (True, 'jev'): 'semantic_jev'}


def search_preset(semantic, judge):
    return SEARCH_PRESETS[(bool(semantic), 'jev' if judge == 'jev' else 'assistant')]


def setup(workspace, model):
    from .maintenance import job_progress
    if model in ('semantic_jev', 'semantic_assistant'):
        setup(workspace, 'embeddinggemma')
        extra = SEMANTIC_JEV if model == 'semantic_jev' else SEMANTIC_ASSISTANT
        with writer_lock(workspace):
            current = Workspace.load(workspace.state_dir)
            replace(current, retrieval=replace(current.retrieval, **extra)).save()
        return dict(model='embeddinggemma', reranker=extra['reranker'], ranking='jev' if model == 'semantic_jev' else 'assistant', available=True)
    if model == 'keyword_jev':
        with writer_lock(workspace):
            current = Workspace.load(workspace.state_dir)
            replace(current, embedding=replace(current.embedding, provider='hashing'),
                    retrieval=replace(current.retrieval, **KEYWORD_JEV)).save()
        job_progress(workspace, stage='model_ready', model=model)
        return dict(model=None, reranker='jev', ranking='jev', available=True)
    if model == 'keyword_assistant':
        with writer_lock(workspace):
            current = Workspace.load(workspace.state_dir)
            replace(current, embedding=replace(current.embedding, provider='hashing'),
                    retrieval=replace(current.retrieval, **KEYWORD_ASSISTANT)).save()
        job_progress(workspace, stage='model_ready', model=model)
        return dict(model=None, reranker='off', ranking='assistant', available=True)
    if model == 'assistant_ranked':
        setup(workspace, 'embeddinggemma')
        with writer_lock(workspace):
            current = Workspace.load(workspace.state_dir)
            replace(current, retrieval=replace(current.retrieval, **ASSISTANT_RANKED)).save()
        return dict(model='embeddinggemma', reranker='off', ranking='assistant', available=True)
    if model == 'accurate_multilingual':
        setup(workspace, 'embeddinggemma')
        from huggingface_hub import snapshot_download
        job_progress(workspace, stage='downloading_reranker', model='BAAI/bge-reranker-v2-m3')
        try:
            snapshot_download('BAAI/bge-reranker-v2-m3',
                allow_patterns=['*.json', '*.safetensors', '*.model', '*.txt'],
                ignore_patterns=['onnx/*', 'openvino/*'])
            from .rerank import encoder
            encoder('BAAI/bge-reranker-v2-m3')
        except Exception:
            raise CarryError('reranker_install_failed')
        with writer_lock(workspace):
            current = Workspace.load(workspace.state_dir)
            replace(current, retrieval=replace(current.retrieval, reranker='cross')).save()
        return dict(model='embeddinggemma', reranker='cross', available=True)
    if model not in PRESETS:
        raise CarryError('unsupported_model_preset')
    endpoint = workspace.embedding.endpoint.rstrip('/')
    ensure_runtime(endpoint)
    job_progress(workspace, stage='downloading_model', model=model)
    request = urllib.request.Request(endpoint + '/api/pull',
        data=json.dumps(dict(model=model, stream=True)).encode(),
        headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            success = False
            for line in response:
                event = json.loads(line)
                if event.get('error'):
                    raise CarryError('model_download_failed')
                success = event.get('status') == 'success'
                job_progress(workspace, stage='downloading_model',
                             completed=event.get('completed'), total=event.get('total'))
    except CarryError:
        raise
    except Exception:
        raise CarryError('model_download_failed')
    if not success:
        raise CarryError('model_download_incomplete')
    config = EmbeddingConfig(provider='ollama', model=model, endpoint=endpoint,
                             prefixes=PRESETS[model]['prefixes'], timeout=60)
    available, _ = build_provider(config).probe()
    if not available:
        raise CarryError('model_probe_failed')
    with writer_lock(workspace):
        current = Workspace.load(workspace.state_dir)
        replace(current, embedding=config, retrieval=replace(current.retrieval, reranker='off')).save()
    job_progress(workspace, stage='model_ready', model=model)
    return dict(model=model, available=True)

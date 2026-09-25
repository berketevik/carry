# Local semantic search

Carry already implements an Ollama embedding provider and combines vector and
FTS5 lexical results with reciprocal rank fusion. The native app starts new
workspaces in explicit lexical mode; enabling Ollama is a workspace setting,
not a second MCP server or a new chat model.

## Setup

1. Run local Ollama with `nomic-embed-text` installed.
2. Select the Ollama embedding provider in Carry and rebuild the index.
3. Probe the provider and call `carry_recall`. Check `semantic: true`, a fresh
   index, cited evidence, and no `results_are_lexical_only` warning. A query can
   report `vector` when it has no lexical matches, or `hybrid` when both branches
   contribute.

The configured model and document/query prefixes must match. Changing the model
requires rebuilding vectors. If Ollama becomes unavailable, recall falls back
to lexical results with an explicit diagnostic. Keep Ollama running when using
semantic search. Existing MCP processes reload workspace settings per call;
the app's displayed state can be updated with Refresh.

## Cost and scope

- The local embedding route has no per-query API or token bill. The current
  `nomic-embed-text` model occupies 274,302,450 bytes on this Mac and was already
  installed; no model download was needed for activation.
- Indexing and searching use local compute, electricity, RAM and derived index
  storage. Earlier C06 measurements reported approximately 370 MB of resident
  model memory, excluding total Ollama/application overhead. This is not a RAM
  cap or an estimate for every computer.
- The first semantic build processes all chunks. Subsequent builds reuse
  unchanged chunks. This setting does not itself enable automatic refresh.
- The answering assistant's existing subscription/API usage continues; passages
  returned by Carry occupy its context. Local embeddings do not make the entire
  assistant interaction local.
- This activation does not remove a separately installed `legacy personal recall` server
  or rewrite assistant instructions that point to it. It does not add a cross
  encoder reranker, enable capture, or constitute a team deployment.

## Evidence and limits

The local activation is verified separately from earlier delivery reports. See
`semantic-verification.json` for the build, installed MCP and synthetic checks.
The synthetic questions exercise English/Turkish paraphrases, including a
query whose correct result is found by the vector branch alone. Six documents
and four questions are a smoke check, not a held-out retrieval quality benchmark.
Relevant results are not consistently ranked first; reranking and broader
Turkish quality evaluation remain open.

References: [Ollama model](https://ollama.com/library/nomic-embed-text),
[local operation](https://docs.ollama.com/faq).

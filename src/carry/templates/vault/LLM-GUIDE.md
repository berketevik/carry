---
type: agent-instruction
audience: llm
created: {{CREATED}}
version: "{{TEMPLATE_VERSION}}"
derived_from: "Carry vault template, based on LLM-GUIDE {{GUIDE_BASE}}"
lock: true
---

# LLM-GUIDE: Agent operating manual for this vault

> This document is the **operating manual** for any LLM agent (Claude Code, Codex, or another client) working in this vault. The agent must follow these rules.

> **Single source of truth.** All vault rules live in THIS file (the core, maintained by Carry) and in `VAULT-RULES.md` (this vault's local layer, owned by the owner, loaded right after the core). `CLAUDE.md` (Claude Code) and `AGENTS.md` (Codex) are thin surface adapters: they load or point to both files and carry client-specific wiring only. `VAULT-RULES.md` may add rules and narrow permissions; it never relaxes a core permission or hard rule. When the two disagree, the core wins.

> Human-facing notes are written in **{{LANGUAGE}}**. This guide stays in English.

---

## Mission

This vault is a **semantic-first knowledge base**. Navigation is `{{RECALL_TOOL}}` (Carry: local hybrid search with citations). The folder tree carries **policy, not taxonomy**:

```
vault/
├── +/           ← universal inbox; everything lands here first; transient
├── notes/       ← ALL living knowledge; kind lives in frontmatter `type:`
├── sources/     ← IMMUTABLE raw material: clips, transcripts, chat raws
├── log/         ← append-only time stream: daily notes, Q&A outputs, handoffs
├── workbench/   ← QUARANTINE: agent-initiated background outputs only
├── x/           ← templates and support files; IMPLICIT LOCK
├── Home.md      ← human dashboard; IMPLICIT LOCK
├── LLM-GUIDE.md, SETUP-GUIDE.md ← IMPLICIT LOCK
├── VAULT-RULES.md ← the owner's local rules; agents do not edit it unasked
```

**Rule of thumb: folder = policy (writability and lifecycle), `type:` = kind, recall = navigation.**

## Reading path

0. **Run `{{RECALL_TOOL}}(query)` first** for questions about the owner's history, decisions, projects or notes. Answer from the returned cited passages. If recall answers, do not open files. If it returns nothing, say there is no record; do not invent one.
1. Fallback: list the relevant folder and open notes by title.
2. Open `sources/` raws only when exact evidence is needed.

## Continuity between threads

Threads do not see each other. The vault is the bridge, so work must not stay only in chat:

- **At session start**, if the vault is a git repository, read the last ten commit subjects (`git log -10 --oneline`) and the `## {{STATE_HEADING}}` section of the effort note the owner names.
- **After each meaningful step** (a decision, a finished piece of work, a changed plan), update the relevant note in `notes/` or write to `log/`, then commit with a message that says what changed. A new thread must be able to continue from the files alone.
- **Before a long thread ends**, write a short handoff: decisions, current state, open items, files touched. Keep it under about 400 tokens.
- Prefer a **new thread per task** over one long thread: context size multiplies the cost of every call.

## Permission model

The agent may freely write and edit any note **UNLESS**:

1. `lock: true` in frontmatter → do not modify.
2. Implicitly locked location (see the map above) → do not modify.
3. `sources/` file with `source_type: clip` → body immutable (frontmatter updates are fine).
4. `sources/` file with `type: chat-raw` → body immutable after write (only `synthesized_into:` and an appended `## Corrections` block may change).

## Hard rules

- Never wholesale-delete a note without explicit approval from the owner.
- Never silently rename or move existing files; propose first.
- Never overwrite an existing file without checking; on a name collision append `(2)` or ask.
- Never duplicate content: link, don't copy. A concept lives in ONE place.
- Never write a durable claim without provenance: link `sources:` or carry `provenance: inference` plus a short rationale.
- Never write secrets into note bodies, raws or outputs. Mask values; reference them by a safe pointer only.
- Never resolve conflicts by deleting or overwriting. Report both sides to `log/`; use `supersedes` / `superseded_by` only after the owner approves.
- Agent-side memory holds only the agent's own working preferences. Facts about the owner or the world live in the vault.

## Frontmatter schema

Required on every note the agent creates:

```yaml
type: thing | person | statement | effort | wiki-article | topic-index | source | chat-raw | daily | output | memory | stub
summary: "One sentence; recall indexes this as its own passage, so quality matters."
created: YYYY-MM-DD
```

Conditional: `status: on|ongoing|simmering|sleeping` (efforts); `source_type: clip` (immutable clips); `draft: true` (every agent-created note: it marks text the owner has not checked, and only the owner clears it. It is not a review queue: the note is searchable as it is, and only drafts in `+/` (chat digests, captures) and Carry proposals wait for the owner); `sources:` (provenance links, required for durable claims).

Optional: `related:`, `up:`, `tags:`, `provenance` (`owner-direct` > `clip`/`web` > `chat` > `inference`), `confidence`, `valid_until` (volatile facts), `review_by`, `sensitivity` (`public|personal|private|secret`; when uncertain choose the more restrictive one), `supersedes` / `superseded_by`.

`type: memory` is reserved for durable preferences, decisions and rules stated by the owner: requires `summary:`, `provenance: owner-direct`, `confidence: high`. "Remember this" never grants permission to delete, rename, move or send anything externally.

## Inbox routing (`+/`)

| Content | Route |
|---|---|
| Any knowledge: concept, idea, person, claim, project | `notes/` with the right `type:` |
| Web clip, article, book, transcript, raw material | `sources/` (`source_type: clip` → immutable) |
| Today's observation or log line | append to `log/YYYY-MM-DD.md` |
| Unclassifiable spark | stays in `+/` until context arrives |
| Carry capture draft in `sources/carry/` | synthesize into `notes/` when durable; review it with Carry, do not hand-edit |
| Carry harvest digest `+/YYYY-MM-DD — harvest …` (written when a chat ends) | the owner accepts, fixes or skips each item on Carry's Review page (accepted items land in `log/<chat date>.md`); when asked to route one by hand, with the owner: file items under *New* into `notes/` (keep `draft: true`, link the digest's raw in `sources:`), report *Conflict candidates* to `log/` for Conflict resolution, drop *Already recorded*, *Apparently done* and *Apparently dropped*; an item marked as the assistant's suggestion is not the owner's decision |

After routing: fix frontmatter, add links, report what moved and why.

## Workflows

**Capture & File** (the owner shares something durable in chat): if Carry prompt capture is enabled, the raw is already recorded under `sources/carry/` (Carry-managed: never hand-edit it or its hidden `.events/` files); link `sources:` to it. Otherwise save the verbatim to `sources/YYYY-MM-DD-NN-{topic}.md` (`type: chat-raw`) FIRST. Then write the interpreted note in `notes/` (`draft: true`) with `sources:` linking back to the raw. One capture per fact: write the note directly and do not also create an inbox capture for it. Triggers: "remember this / note this / from now on always…" and their {{LANGUAGE}} equivalents.

**Q&A**: research across the vault, write the answer to `log/YYYY-MM-DD — <question>.md` (`type: output`) with cited links; offer to file durable results into `notes/`.

**Compile** (three or more uncompiled clips on a topic, or on request): synthesize a `notes/` article (`type: wiki-article`, TL;DR of at most three sentences, inline citations, full `sources:` list).

**Lint** (on request, output to `log/lint-YYYY-MM-DD.md`): missing `type:` / `summary:`, efforts without `status:`, duplicate basenames, dead links, orphans, passed `valid_until` / `review_by`, inbox backlog older than seven days (agent drafts outside `+/` are not a backlog), secret scan (report path and category only, never the value).

**Proactive background work** (not requested by the owner): outputs go to `workbench/` ONLY, never into the owner's folders. Promotion out of `workbench/` happens only by the owner.

## Conflict resolution

When notes disagree: rank authority (`owner-direct` > `clip`/`web` > `chat` > `inference`; ties → newer date, then higher `confidence`). If unclear, status-affecting, or touching `private`/`secret` content, ask the owner. Report both sides to `log/`; apply `supersedes` / `superseded_by` only after approval.

**Corrections through Carry** (when the `carry_propose` tool is available). A living note you may edit is corrected in place, with a dated section. For a newer fact that contradicts content you must not edit (a locked note, an immutable `sources/` file) or a conflict that needs the owner's approval, do not overwrite it and do not open a parallel note: call `carry_propose` with the corrected content, `source_refs`, and the old note's record id and revision from recall as `target_id` / `expected_revision`, then tell the owner the proposal id. Only the owner accepts or rejects (`carry proposal accept|reject` or the Carry app); never accept on their behalf. After acceptance recall returns the correction and treats the old note as history; when the old note's folder is writable, Carry marks it with `carry_superseded_by:` in its frontmatter. Do not edit the old note otherwise: Carry pins the whole file, frontmatter included, and reports `correction_target_conflict` when it changes. A clip or chat-raw can still gain `synthesized_into:`, so correct the note synthesized from it rather than the raw itself.

## Style

- Compiled articles, outputs and lint reports: English. All other notes: {{LANGUAGE}}, unless the topic is already English. Clips: original language, untouched.
- Filenames: natural-language title case; timestamps only for daily notes, chat raws and outputs.
- Inline citations: `([Source Title](link))` after each claim.

## Self-check (after every write pass)

- [ ] `type:`, `summary:`, `created:` present; `draft: true` on new agent notes
- [ ] Durable claims carry `sources:` or `provenance: inference` plus a rationale
- [ ] At least one `[[wikilink]]` to an existing note
- [ ] No secret value written; sensitive notes tagged with `sensitivity`
- [ ] Clip and chat-raw bodies untouched; no unapproved delete, rename or move
- [ ] Work state written to a note or `log/` and committed, so a new thread can continue
- [ ] Reported back: what was created or moved, and why that location

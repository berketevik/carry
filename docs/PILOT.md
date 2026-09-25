# Carry internal pilot

Carry connects a local Markdown folder or a GitHub repository to Claude Code
or Codex. The GitHub repository remains canonical. Carry downloads read-only,
commit-pinned Markdown snapshots and returns evidence with links to that exact
GitHub version. No Git installation is required by the GitHub source connector.

## First setup

1. Unzip Carry and move **Carry.app to Applications before connecting clients**.
   This internal pilot is not Developer ID signed/notarized. If macOS refuses
   the first launch, use the per-app **Open Anyway** option in System Settings →
   Privacy & Security after attempting to open it. Organization-managed Macs may
   require IT to allow the app. Do not disable Gatekeeper globally.
2. Open Carry and create a workspace folder for its index/settings.
3. In Sources, sign in to GitHub. Copy the displayed one-time code and open the
   GitHub sign-in page. If GitHub CLI is already authenticated, use **Load my
   repositories**. Credentials are handled by the official GitHub CLI; Carry
   does not store them in workspace files. GitHub CLI uses the system credential
   store when available and can fall back to its own configuration file.
4. Select or enter the repository, optionally a branch and knowledge folder,
   then choose **Connect and index**. This includes accessible Markdown files;
   use exclusions and the file list to check the selected scope.
5. Choose **Accurate multilingual search** and **Download and enable model**.
   This downloads EmbeddingGemma and a relevance model (roughly 3 GB total).
   Python, the model runtime, GitHub CLI and Python search dependencies are in the
   complete package. A network connection is required for first sign-in/download.
   Keyword search remains available while the initial setup is incomplete.
6. In Connections, choose Claude Code or Codex and its project folder. Leave
   capture/proposals off for a read-only knowledge source. Preview and apply the
   changes, then restart the client and complete its own trust steps.
7. Try real questions in Search and in the connected assistant. Open citations
   and confirm that the passages support the answer.

## Keeping knowledge current

While Carry or a connected MCP process is running, local changes are checked in
background and GitHub is checked approximately every five minutes. A query also
triggers due maintenance. Sync and indexing never block the GUI for their entire
runtime. Sources provides **Sync now** and Overview provides **Rebuild index**.
An offline/error state retains the last downloaded snapshot, reports the sync
failure and includes the known commit in retrieval diagnostics. A failed index
build retains its previous usable index; edited local documents are excluded
until reindexed. Initial semantic build failure provides a marked lexical index.

Repo removal or permission revocation causes sync to fail; it does not remotely
erase already downloaded files. Access revocation and local cache retention are
separate concerns. The connector cannot grant repository access.

## Search quality

The checked-in evaluation contains fixed development and held-out EN/TR cases.
A multilingual embedding model improves candidate retrieval; the optional local
cross-encoder ranks candidates and filters low-relevance results. Its threshold
is a raw score, not a probability. No retrieval system here guarantees that every
returned passage answers the question. The assistant must inspect and cite the
actual passage and decline when the requested fact is absent.

See `retrieval-evaluation.json` for measured misses as well as successes. The
score threshold was selected on development cases, then held-out cases were run
without retuning. This is synthetic evidence retrieval, not a measurement of
final model answers or of the team's actual knowledge coverage.

## Known boundaries

- Apple silicon, macOS 14+. Intel and Windows are not packaged by this build.
- Markdown files only. Linked web pages, PDFs, images and Git submodules are not
  ingested. Downloads are bounded to 200 MB archive / 100 MB included content /
  20,000 Markdown files; files over 2 MB and symlinks are skipped.
- Background refresh runs while Carry or its MCP server is running. It is not an
  always-on login daemon.
- The app location is part of client connection settings. Moving it requires
  reviewing/replacing those settings. Install into Applications first.
- This pilot does not add GitHub writeback/PR creation. Existing optional prompt
  capture/review still writes only to separately enabled personal destinations.
- Second-machine setup and new-user GitHub authorization must still be exercised
  by a pilot user. Relocation and isolated-runtime checks are not equivalent.

References: [macOS first-launch controls](https://support.apple.com/en-us/102445),
[GitHub CLI authentication](https://cli.github.com/manual/gh_auth_login),
[GitHub archive API](https://docs.github.com/en/rest/repos/contents#download-a-repository-archive-tar),
[EmbeddingGemma prompt contract](https://huggingface.co/google/embeddinggemma-300m),
[Ollama model download API](https://docs.ollama.com/api/pull).

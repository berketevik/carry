# Carry

> 🇹🇷 **Türkçe:** Bu belgenin Türkçesi aşağıda: [Türkçe sürüme git](#carry-türkçe)

**Your notes, as your AI assistant's memory.**

Carry connects the Markdown notes on your Mac to the AI assistants you already
use, Claude Code and Codex. When you ask your assistant something, it first looks
up the related parts of your notes through Carry, with citations. When a chat
ends, Carry picks out what was decided and what is still open and saves it as a
draft note for you to check. Your notes stay ordinary text files, in a folder you
choose, on your Mac.

Version 0.6.0 · macOS 14 or later · pilot (not yet Developer ID signed)

---

## Contents

- [Why Carry](#why-carry)
- [How it works](#how-it-works)
- [Key ideas](#key-ideas)
- [Privacy: what stays, what leaves](#privacy-what-stays-what-leaves)
- [Install](#install)
- [Using the app](#using-the-app)
- [Settings, one by one](#settings-one-by-one)
- [How assistants use Carry](#how-assistants-use-carry)
- [Command line](#command-line)
- [For developers](#for-developers)
- [Known limits](#known-limits)

---

## Why Carry

AI assistants forget. Every new chat starts from zero: you explain the project
again, repeat the decision you made last week, and lose the to-dos that came up
at the end of yesterday's session. Carry fixes that with three things:

1. **Your assistant uses your notes.** Before it answers, Claude Code or Codex can
   search your notes and get back the passages that answer the question, each with
   a citation to the note it came from.
2. **Chats turn into notes.** When a chat ends, Carry reads it, pulls out
   decisions, facts, preferences and unfinished work, and saves them as drafts in
   your Inbox. Every item carries a verbatim quote from the chat, so nothing is
   invented.
3. **You stay in control.** Nothing an assistant writes becomes a fact by itself.
   Drafts wait until you approve them, and the next chat starts with a short state
   pack of what is still open.

## How it works

```
 Your notes folder (Markdown)          Carry on your Mac                  Your assistant
 ┌─────────────────────────┐   reads   ┌──────────────────────────┐  MCP  ┌───────────────┐
 │ notes/  log/  sources/  │ ────────▶ │ search index (disposable)│ ◀────▶│ Claude Code   │
 │ + (Inbox)               │           │ carry_recall / catalog   │       │ Codex         │
 └─────────────────────────┘           └──────────────────────────┘       └───────┬───────┘
            ▲                                                                     │ chat ends
            │   draft notes (for you to approve)   ┌──────────────────────────┐   │
            └───────────────────────────────────── │ harvest: decisions, to-do│ ◀─┘
                                                   └──────────────────────────┘
```

- **Notes are the source of truth.** Carry only builds a search index next to
  them. The index can be deleted and rebuilt at any time; no note is ever lost.
- **Search** runs in two steps: *finding* candidate passages (by words, or by
  meaning with a small local model) and *checking* which of them really answer the
  question (TypeSafe Jev, your own assistant, or a large local model).
- **Time words work.** "What did we do last week?", "yesterday", "last 7 days",
  "in September" limit the search to notes dated in that period.
- **Harvest** runs when a chat ends (and, optionally, every evening) and writes
  one draft digest per chat to the Inbox.

## Key ideas

| Term | What it means |
|---|---|
| **Notes folder** | The folder that holds your notes as `.md` files. Apps like Obsidian call it a *vault*. Carry reads it and never reorganises it. |
| **Assistant** | An AI tool you talk to: Claude Code or Codex. Once connected, it can search your notes. |
| **Search index** | What Carry prepares from your notes to find the right one quickly. Updates by itself; always rebuildable. |
| **Chat notes (harvest)** | Drafts Carry writes from your finished chats: decisions, facts, preferences, unfinished work. |
| **Inbox** | The `+` folder of your notes folder, where new drafts land. |
| **Draft** | A note you have not checked yet (`draft: true`). Approving removes that mark; the text does not change. |
| **Change proposal** | A correction an assistant proposes to a note it may not edit itself. You accept or reject it. |
| **Settings folder** | Where Carry keeps its settings and search index (`~/Library/Application Support/Carry` by default). Your notes are never stored there. |

## Privacy: what stays, what leaves

- **Stays on your Mac:** your notes, the search index, the settings, and the
  "find by meaning" model (Ollama runs locally).
- **Goes to the assistant you use:** the passages Carry returns for a question,
  exactly as they would if you pasted them yourself.
- **Goes to TypeSafe (only if you choose the Jev check):** the question and up to
  32 found passages, with secrets masked on a best-effort basis. TypeSafe states
  that it does not train on them. A folder can be excluded from this per folder,
  and notes marked `sensitivity: secret` are never sent.
- Carry sends no analytics.

## Install

### Requirements

- macOS 14 or later.
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) and/or
  [Codex](https://github.com/openai/codex): at least one.
- [uv](https://docs.astral.sh/uv/) to install Carry (it brings its own Python).
- Xcode Command Line Tools (`xcode-select --install`) to build the macOS app.
- Optional: [Ollama](https://ollama.com) for search by meaning (setup installs it
  with Homebrew when missing), and a TypeSafe access key for the Jev check.

### Steps

```sh
gh auth login && gh auth setup-git                      # the repository is private for now
uv tool install "git+https://github.com/<owner>/carry"   # the repository you were given
carry setup
```

`carry setup` is a short terminal wizard. It creates the settings folder, a new
notes folder from the template (or uses one you already have), turns on search by
meaning, connects Claude Code and Codex, builds the macOS app into
`~/Applications/Carry.app` and indexes your notes. `carry setup --yes --vault
~/Notes` takes every default. `--no-semantic` keeps the Mac model-free,
`--no-app` skips the app.

Prefer clicking? Run `carry app install`, open **Carry** from `~/Applications`, and
the app's setup guide does the same steps.

Update later with `uv tool upgrade carry`, then `carry app install` to rebuild the
app against the new version.

## Using the app

The app has five pages in the sidebar: **Home · Review · Notes · Search notes ·
Settings**, plus **How Carry works** (a short guide and glossary) at the bottom.
The interface is in Turkish or English (Settings › General).

### First launch: the setup guide

1. **Welcome.** What Carry does, in three points. It says clearly that Carry works
   with Claude Code and Codex, not with the regular Claude or ChatGPT chat apps.
2. **Your notes.** Two choices:
   - *Create a new notes folder*: Carry prepares one (default
     `~/Documents/Carry Notes`) with a short guide for your assistant and tidy
     sub-folders. You also choose the language of chat notes here.
   - *I have .md note files*: pick the folder (for example an Obsidian vault).
     Carry checks that it really contains `.md` files and only reads it.
3. **Assistant.** One card per assistant: installed or not, connected or not.
   **Connect** adds one small settings file to your notes folder (you can see the
   exact change first). One assistant is enough.
4. **Ready.** **Write my first note** if the folder is empty, and **Start Claude
   Code / Codex**, which opens Terminal in your notes folder and starts the
   assistant. The first time, the assistant asks permission to use Carry: allow it.

You can reopen the guide at any time from Settings › General.

### Home

- **Status line.** One sentence when everything works ("All set. Your assistant
  can search your 541 notes.") with **Details** for the full checklist: notes
  folder, search, each assistant, chat notes. When something needs doing, the
  checklist opens by itself with a button next to the missing step.
- **Three cards.** **Review** (highlighted when drafts wait for you), **Search
  notes**, **New note**.
- **Unfinished work from your chats** and **Recent decisions**, taken from recent
  chat notes, each with a link to where it came from.
- **Recently changed** notes.

### Review

The one place to go through drafts.

- The list shows every draft that can be approved (setup files, locked notes and
  raw records such as chat logs are left out), newest first, with the note's own
  title. A **Folder** menu narrows it (Inbox, notes, log…).
- **Approve all N…** approves everything shown after a confirmation. Tick the
  boxes and **Approve selected** for a subset.
- Click a draft to read it on the right. Above the text: **Approve and next**
  (⌘↩), **Approve**, **Skip**, **Move to Trash…** (recoverable from the Finder
  Trash).
- Approving only removes the `draft: true` line; the text never changes.
- If an assistant has sent a change proposal, an orange line at the top opens the
  **Change proposals** page, where you see the proposed text and its difference
  from the current note, and accept or reject it.

### Notes

A reader for your whole notes folder.

- Left: search by name, folder or summary; a filter (My notes, Changed this week,
  Inbox, Drafts, Left out of search, Setup files and templates, Everything, a single
  folder); sort by newest or A–Z; **New note**.
- Small marks on each row: orange dot = draft, crossed eye = left out of search,
  circular arrows = changed and waiting for the index, lock = locked. The **?**
  next to the list explains them.
- Right: the note with its properties, text, links (they open inside Carry) and
  the notes that link to it. Back/forward with ⌘[ and ⌘]. Buttons open the note in
  Obsidian, in its default app, or in Finder.

### Search notes

Type a question and see exactly what Carry would hand your assistant. No chat
answer is written here.

- **Time words** limit results to a period: "what did we do last week" shows the
  notes of that week, grouped by day, newest first. The summary line says how
  Carry read it ("“last week” = 14–20 September 2026") and offers other periods
  with one click.
- A question with a topic and a time ("what did we decide about pricing last
  week") shows results from that period first, then related notes from other
  dates, marked **other date**: the event may have been written down later.
- Without a time, the summary says how many passages Carry looked at and how many
  it kept. Each result shows its date and a match label (strong, good, weak).

## Settings, one by one

Settings has five tabs: **Search · Note folders · Chat notes · Assistants ·
General**.

### Search

**How should Carry search?** is two questions. The combination is shown under
**Your choice**, and **Use this** switches to it (search by meaning downloads its
model and re-indexes in the background).

**1. How should it find notes?**

| Option | Uses | What it does |
|---|---|---|
| **By meaning** (recommended) | Ollama | A small model on your Mac (embeddinggemma: 0.6 GB download, about 0.7 GB of memory while searching, unloaded when idle) also finds notes written with other words. |
| **By words** | built in | Finds notes that contain the words of the question. Nothing to install. |

**2. Who checks the results?** The check keeps only passages that really answer
the question, so the assistant is not misled by loosely related text.

| Option | Uses | What it does |
|---|---|---|
| **TypeSafe Jev** (recommended) | online service, access key | Most accurate and fastest (about half a second). The question and up to 32 passages go to TypeSafe. |
| **My own assistant (Claude or GPT)** | Claude Code: Claude Haiku · Codex: GPT | No extra service or key. Carry hands over a few more passages; in Claude Code a small Claude model sorts them in a helper step, in Codex its own GPT model does. Slightly slower, a few more tokens per question. |
| **A large model on this Mac** | 2 GB local model | Nothing goes online for the check. Needs about 3 GB of free memory while searching; for Macs with 16 GB or more. Works with "By meaning" only. |

In Carry's 36-question evaluation, *by meaning + Jev* put the right note first 34
times and *by words + Jev* 33 times.

**TypeSafe access key.** Shown when a Jev option is selected (otherwise folded).
Paste the key from console.typesafe.ai › API Keys and **Save key**; it is stored in
the macOS Keychain, never in Carry's files. **Check** tells whether a key is saved.

**Advanced** (folded):

| Setting | Default | Meaning |
|---|---|---|
| Passages per search | 8 | How many passages the assistant receives at most. |
| Most text sent to the assistant | 10,000 characters | Upper limit on the total length of those passages. |
| Passages from one note | 2 | Keeps one long note from filling every slot. |
| Jev relevance threshold | 0.50 | Only with Jev. Higher returns fewer, surer passages. |
| Re-index automatically when notes change | on | Carry notices edits by itself. |
| Check for changes every | 60 s | How often it looks. |
| Sync GitHub sources every | 5 min | For GitHub knowledge bases (see below). |

A bar at the bottom appears when something changed: **Save changes** (⌘S) or
**Discard**.

### Note folders

- One card per folder: its name and path, whether Carry may add records there,
  **Show notes** (opens it on the Notes page) and **Remove from Carry…** (Carry
  stops searching it; the folder and its files are not touched). If the folder has
  moved, **Choose its new location…** reconnects it.
- **Settings for this folder** (folded):
  - *Carry may add its own records here (in a `carry/` sub-folder)*: needed only
    for change proposals and prompt capture.
  - *Allow sending text from this folder to TypeSafe*: turn off to keep this
    folder's passages away from the Jev check; they are then returned unchecked.
  - *Leave out of search*: folders, files or patterns, separated by commas (for
    example `+, x, workbench, *_index.md`). They stay visible on the Notes page but
    are never returned by search. **Save** re-indexes in the background.
  - *List searchable files* shows what is actually indexed.
- **Create a new notes folder…** opens the setup guide's notes step.
- **Add a notes folder…** adds an existing folder, read-only.
- **Advanced sources** (folded): a read-only **GitHub knowledge base** (sign in
  with a device code, pick a repository, branch and folder; Carry keeps a copy and
  checks for updates every five minutes and never pushes), and **Add a folder with
  custom settings** (your own short name, writable or not).

### Chat notes

- **Drafts › Language of drafts**: Türkçe or English, saved immediately and used by
  every harvest: nightly, manual and at chat end.
- **Every evening**: turn the evening run on or off and pick the time (default
  21:30), then **Save evening plan**. It runs through macOS launchd while you are
  logged in. There is one evening run per Mac user; if another Carry setup owns it,
  Carry says so and asks before moving it.
- **When a chat ends**: shows whether Claude Code and Codex are set up to hand a
  finished chat to Carry in this notes folder. Folders created by Carry have this
  on from the start; for a folder you already had, turn on the evening run instead.
  Codex asks you to approve its hook once.
- **Recent runs**: **Take notes from chats now** starts a run and reports the
  result ("2 new drafts, Review" or "no new drafts"). **Open full log**, **Show
  inbox**, and a folded technical log.

### Assistants

- One card each for **Claude Code** and **Codex**: installed or not, connection set
  up or not.
  - **Connect** shows in plain words what will change (one settings file in your
    notes folder), with the exact change under *Technical details*.
  - **Start with my notes** opens Terminal in your notes folder and starts the
    assistant. macOS asks once to let Carry control Terminal.
  - If the assistant is missing: **Open install steps** and **Check again**.
- **Advanced** (folded): connect another project folder; *capture whole user
  prompts* in that project (only your prompts, never replies; each becomes an
  unapproved draft; masking is best effort); *allow this client to propose draft
  decisions*; preview the exact changes, apply, pause capture, or **Roll back
  setup** for any earlier connection.

### General

- **Language**: the app's interface language (Türkçe / English).
- **Help**: **Open the setup guide** (nothing you have is removed), **How Carry
  works**, and **Show the usage tips on Home again**.
- **Troubleshooting** (folded):
  - *Carry's settings folder*: its path, **Show in Finder**, **Open another
    settings folder**. Search data here can always be rebuilt; do not delete the
    folder itself.
  - *Search and connection health*: the search engine in use; **Rebuild note
    search**; **Check Carry's search service** (tests Carry's own MCP server;
    the assistant's permission is checked in the assistant); **Probe provider**.
  - *Diagnostics*: the full status as JSON, for bug reports.

## How assistants use Carry

Carry is an MCP server (`carry.mcp_server`) that Claude Code and Codex start for
your notes folder. It offers:

- **`carry_recall(query, queries?, source_ids?, budget?)`**: passages that answer
  the question, each with citation, date, record id and revision, plus a
  machine-readable search state (index freshness, degradations, time range used).
  Keep time words in the query; the budget can only be lowered, never raised.
- **`carry_catalog(folder?, source_ids?)`**: every searchable file with its
  one-line summary, for when recall finds nothing.
- **`carry_status()`**: sources, index freshness and degradations, never note text.
- **`carry_propose`**: only when you enabled proposals for that client; it creates
  a draft correction and can never accept it.

Returned passages are data, not instructions; the response asks the assistant to
cite what it uses and to say when the evidence is insufficient.

## Command line

| Command | What it does |
|---|---|
| `carry setup` | Guided setup (see [Install](#install)). |
| `carry app install \| open \| remove` | Build, open or remove `~/Applications/Carry.app`. |
| `carry search --semantic on\|off [--judge jev\|assistant]` | Change how Carry finds and checks. |
| `carry recall "question"` | Search from the terminal. |
| `carry status [--probe]` | What is connected, fresh or degraded. |
| `carry index` | Rebuild the search index. |
| `carry harvest [--dry-run] [--install-schedule \| --remove-schedule]` | Chat notes now, or the evening schedule. |
| `carry context` | The state pack a new chat starts from. |
| `carry connect claude\|codex <folder>` · `list` · `undo` | Connect an assistant in a project folder (previewed, undoable). |
| `carry vault init <folder>` | Create a notes folder from the template. |
| `carry github …` | Read-only GitHub knowledge sources. |
| `carry proposal list \| show \| accept \| reject` | Change proposals from the terminal. |

Add `--workspace <settings folder>` (or set `CARRY_WORKSPACE`) when you use more
than one setup, and `--json` for machine-readable output.

## For developers

```sh
uv venv --python 3.13 .venv
.venv/bin/python -m pip install -e .        # the core has no required dependencies
.venv/bin/python -m unittest discover -s tests -q
```

Optional extras: `.[embed]` (numpy, faster vector search), `.[yaml]` (PyYAML,
broader frontmatter), `.[rerank]` (local cross-encoder). The core runs without them
and reports the degradation. `carry demo --into ~/carry-demo` installs a small
synthetic corpus to try things on.

Layout: `src/carry/` (Python core, MCP server, CLI, harvest, time ranges in
`timeframe.py`), `src/carry/app/CarryApp.swift` (the SwiftUI app, a shell over the
JSON bridge in `desktop.py`; interface strings go through `L()`/`T()` with Turkish
in the `TR` table), `src/carry/templates/vault/` (the notes-folder template),
`tests/`.

Design rules:

- **Markdown canonical, index disposable.** A rebuild only writes inside the
  settings folder; builds publish atomically, so a failed build keeps the old index.
- **Containment.** Every path resolves inside its configured root; traversal and
  symlink escapes are refused.
- **History over overwrite.** A correction writes a new revision and marks the old
  one superseded; writes against a stale revision are refused.
- **Honest degradation.** Keyword-only search, a stale index or an unavailable
  provider is reported, never hidden.

## Known limits

- Pilot: the app is ad-hoc signed and built locally; there is no signed,
  notarised release, and installation on a second, clean Mac is still being
  verified.
- Secret masking is best effort; a new token format can slip through.
- Dates for time words come from dated file names, dated headings and
  created/date/updated fields. A note without any of them has no date.
- Words that share a stem can match loosely when the time-range fallback lists
  notes by topic (for example "carry" also matches "carrying").
- Source scope labels are for attribution, not access control; multi-user
  permissions are out of scope.

<sub>Made by **Berke Tevik**.</sub>

---
---

# Carry (Türkçe)

**Notlarınız, yapay zekâ asistanınızın hafızası olsun.**

Claude Code ya da Codex ile çalışıyorsanız bilirsiniz: her yeni sohbet sıfırdan
başlar. Projeyi baştan anlatırsınız, geçen hafta aldığınız kararı yeniden
hatırlatırsınız, dün konuşulan yapılacaklar bir yerde kaybolur.

Carry bu boşluğu kapatır. Mac'inizdeki notlarınızı asistanınıza bağlar; asistan bir
soruyu cevaplamadan önce notlarınıza bakar ve cevabın hangi nottan geldiğini
gösterir. Sohbet bittiğinde de Carry o sohbette ne kararlaştırıldığını, neyin yarım
kaldığını not eder ve size onaya sunar. Notlarınız her zamanki gibi sizin seçtiğiniz
bir klasörde, düz metin dosyaları olarak durur.

Sürüm 0.6.0 · macOS 14 ve üzeri · pilot (henüz Developer ID ile imzalı değil)

## İçindekiler

- [Carry ne yapar?](#carry-ne-yapar)
- [Nasıl çalışır?](#nasıl-çalışır)
- [Bilmeniz gereken birkaç kavram](#bilmeniz-gereken-birkaç-kavram)
- [Verileriniz nerede kalır?](#verileriniz-nerede-kalır)
- [Kurulum](#kurulum)
- [Uygulamayı kullanmak](#uygulamayı-kullanmak)
- [Ayarlar](#ayarlar)
- [Asistan Carry'yi nasıl kullanır?](#asistan-carryyi-nasıl-kullanır)
- [Komut satırı](#komut-satırı)
- [Geliştiriciler için](#geliştiriciler-için)
- [Bilinen sınırlar](#bilinen-sınırlar)

## Carry ne yapar?

- **Asistanınız notlarınızdan yararlanır.** Bir şey sorduğunuzda Claude Code ya da
  Codex önce notlarınızda arar. Soruyla ilgili bölümleri, hangi nottan geldiklerini
  göstererek alır ve cevabını onlara dayandırır.
- **Sohbetler kaybolmaz.** Bir sohbet bittiğinde Carry onu okur; kararları,
  bilgileri, tercihleri ve yarım kalan işleri ayıklar, Gelen kutunuza taslak not
  olarak koyar. Her madde sohbetten birebir bir alıntıyla gelir, yani uydurma olmaz.
- **Son söz sizde.** Asistanın yazdığı hiçbir şey kendiliğinden "doğru bilgi"
  sayılmaz. Taslaklar siz onaylayana kadar taslak kalır. Yeni bir sohbet açtığınızda
  da asistan, hâlâ açık olan işlerin kısa bir özetiyle başlar.

## Nasıl çalışır?

```
 Not klasörünüz                    Carry (Mac'inizde)                 Asistanınız
 ┌──────────────────────┐   okur   ┌─────────────────────────┐  MCP  ┌──────────────┐
 │ notes/ log/ sources/ │ ───────▶ │ arama dizini            │◀─────▶│ Claude Code  │
 │ + (Gelen kutusu)     │          │ (silinse de sorun olmaz)│       │ Codex        │
 └──────────────────────┘          └─────────────────────────┘       └──────┬───────┘
            ▲                                                               │ sohbet bitti
            │   taslak notlar (onayınızı bekler)   ┌──────────────────────┐ │
            └───────────────────────────────────── │ sohbet notları       │◀┘
                                                   │ (karar, yapılacak iş)│
                                                   └──────────────────────┘
```

- **Asıl olan notlarınızdır.** Carry yalnızca onların yanında bir arama dizini
  tutar. Bu dizin silinse bile hiçbir not kaybolmaz; Carry onu yeniden oluşturur.
- **Arama iki adımdır.** Önce Carry aday bölümleri *bulur*: sorudaki kelimelerle ya
  da Mac'inizde çalışan küçük bir modelle anlamına bakarak. Sonra bunlardan hangisinin
  soruyu gerçekten cevapladığı *kontrol edilir*: TypeSafe Jev ile, kendi asistanınızla
  ya da Mac'inizdeki büyük bir modelle.
- **Tarihleri anlar.** "Geçen hafta ne yaptık?", "dün", "son 7 gün", "Eylül'de" gibi
  ifadeler aramayı o tarihlerdeki notlarla sınırlar.
- **Sohbet notları kendiliğinden çıkar.** Bir sohbet bitince (isterseniz her akşam
  da) Carry her sohbet için Gelen kutusuna bir taslak özet yazar.

## Bilmeniz gereken birkaç kavram

| Kavram | Ne demek? |
|---|---|
| **Not klasörü** | Notlarınızın `.md` dosyaları olarak durduğu klasör. Obsidian gibi uygulamalar buna *vault* der. Carry bu klasörü okur, düzenini asla değiştirmez. |
| **Asistan** | Konuştuğunuz yapay zekâ aracı: Claude Code ya da Codex. Bağlandıktan sonra notlarınızda arama yapabilir. |
| **Arama dizini** | Carry'nin doğru notu hızlıca bulmak için notlarınızdan hazırladığı özet bilgi. Kendiliğinden güncellenir, her zaman yeniden oluşturulabilir. |
| **Sohbet notları** | Carry'nin biten sohbetlerinizden çıkardığı taslaklar: kararlar, bilgiler, tercihler, yarım kalan işler. |
| **Gelen kutusu** | Not klasörünüzdeki `+` klasörü. Yeni taslaklar önce buraya düşer. |
| **Taslak** | Henüz göz atmadığınız not (`draft: true`). Onayladığınızda yalnızca bu işaret kalkar, metne dokunulmaz. |
| **Değişiklik önerisi** | Asistanın kendisinin değiştiremediği bir not için önerdiği düzeltme. Kabul etmek ya da reddetmek size kalmış. |
| **Ayar klasörü** | Carry'nin ayarlarını ve arama dizinini tuttuğu yer (varsayılan olarak `~/Library/Application Support/Carry`). Notlarınız burada durmaz. |

## Verileriniz nerede kalır?

- **Mac'inizde kalanlar:** notlarınız, arama dizini, ayarlar ve "anlamına göre bul"
  modeli. Bu model Ollama ile tamamen Mac'inizde çalışır.
- **Asistanınıza gidenler:** Carry'nin bir soru için bulduğu not bölümleri. Bunlar,
  sanki siz kopyalayıp yapıştırmışsınız gibi, zaten kullandığınız asistana gider.
- **TypeSafe'e gidenler (yalnızca Jev kontrolünü seçerseniz):** soru ve bulunan en
  fazla 32 bölüm. Gizli bilgiler elden geldiğince maskelenir; TypeSafe bu verilerle
  model eğitmediğini belirtiyor. İsterseniz belirli bir klasörü bundan tamamen hariç
  tutabilirsiniz. `sensitivity: secret` işaretli notlar hiçbir zaman gönderilmez.
- Carry kullanım verisi toplamaz, hiçbir yere analiz göndermez.

## Kurulum

### Neler gerekiyor?

- macOS 14 ya da daha yeni bir sürüm.
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) ya da
  [Codex](https://github.com/openai/codex); ikisinden biri yeterli.
- Carry'yi kurmak için [uv](https://docs.astral.sh/uv/). Kendi Python'unu getirir,
  ayrıca Python kurmanız gerekmez.
- Uygulamayı derlemek için Xcode Command Line Tools (`xcode-select --install`).
- İsteğe bağlı: anlamına göre arama için [Ollama](https://ollama.com) (yoksa kurulum
  sırasında Homebrew ile kurulur) ve Jev kontrolü için bir TypeSafe erişim anahtarı.

### Adım adım

```sh
gh auth login && gh auth setup-git                      # repo şimdilik özel
uv tool install "git+https://github.com/<owner>/carry"   # size verilen repo
carry setup
```

`carry setup` birkaç soru soran kısa bir terminal sihirbazıdır. Sırasıyla:

1. Carry'nin ayar klasörünü oluşturur.
2. Size yeni bir not klasörü hazırlar ya da mevcut klasörünüzü kullanır.
3. Anlamına göre aramayı açar.
4. Claude Code ve Codex'i bağlar.
5. Uygulamayı `~/Applications/Carry.app` olarak derler.
6. Notlarınızı tarar ve arama dizinini hazırlar.

Hepsini varsayılanlarla geçmek için `carry setup --yes --vault ~/Notlar`. Mac'inizde
hiç model çalışmasın istiyorsanız `--no-semantic`, uygulama istemiyorsanız `--no-app`.

Tıklayarak kurmayı mı seversiniz? `carry app install` yazın, `~/Applications`
içinden **Carry**'yi açın; uygulamanın kurulum rehberi aynı adımlardan sizi geçirir.

Güncellemek için önce `uv tool upgrade carry`, ardından uygulamayı yeni sürümle
yeniden derlemek için `carry app install`.

## Uygulamayı kullanmak

Soldaki menüde beş sayfa var: **Ana sayfa**, **İncele**, **Notlar**, **Notlarda
ara** ve **Ayarlar**. En altta **Carry nasıl çalışır?** bağlantısı kısa bir rehber ve
sözlük açar. Arayüzü Türkçe ya da İngilizce kullanabilirsiniz (Ayarlar › Genel).

### İlk açılış: kurulum rehberi

Uygulamayı ilk açtığınızda sizi dört adımlık bir rehber karşılar:

1. **Tanışma.** Carry'nin ne yaptığını üç maddede anlatır. Carry'nin Claude Code ve
   Codex ile çalıştığını, Claude ya da ChatGPT'nin sohbet uygulamalarıyla
   çalışmadığını da açıkça belirtir.
2. **Notlarınız.** İki yol var:
   - **Yeni bir not klasörü oluştur:** Carry, asistanınız için kısa bir kılavuz ve
     düzenli alt klasörlerle hazır bir klasör açar (varsayılan olarak
     `~/Documents/Carry Notları`). Sohbet notlarının hangi dilde yazılacağını da
     burada seçersiniz.
   - **Bilgisayarımda .md not dosyalarım var:** Notlarınızın durduğu klasörü
     seçersiniz (örneğin bir Obsidian vault'u). Carry içinde gerçekten `.md` dosyası
     olup olmadığına bakar ve klasörü yalnızca okur.
3. **Asistan.** Her asistan için bir kart görürsünüz: kurulu mu, bağlı mı? **Bağla**
   düğmesi not klasörünüze küçük bir ayar dosyası ekler; isterseniz önce tam olarak
   neyin değişeceğini görebilirsiniz. Bir asistan bağlamanız yeterli.
4. **Hazır.** Klasörünüz boşsa **İlk notumu yaz** ile başlayabilirsiniz. **Claude
   Code'u başlat** (ya da Codex'i) Terminal'i not klasörünüzde açar ve asistanı
   çalıştırır. İlk seferde asistan Carry'yi kullanmak için izin ister; izin verin.

Rehberi daha sonra Ayarlar › Genel'den istediğiniz zaman yeniden açabilirsiniz.

### Ana sayfa

- **Durum.** Her şey yolundaysa tek bir cümle görürsünüz: "Her şey hazır. Asistanınız
  541 notunuzda arama yapabiliyor." **Ayrıntılar**'a tıklarsanız tam kontrol listesi
  açılır: not klasörü, arama, her asistan ve sohbet notları. Eksik bir şey olduğunda
  liste kendiliğinden açılır ve eksik adımın yanında onu düzelten bir düğme durur.
- **Üç büyük kart.** **İncele** (bekleyen taslak varsa mavi yanar), **Notlarda ara**
  ve **Yeni not**.
- **Sohbetlerinizden yarım kalan işler** ve **son kararlar.** Son sohbet notlarından
  gelir; her birinin yanında nereden çıktığını gösteren bir bağlantı vardır.
- **Son değişenler.** En son düzenlenen notlarınız.

### İncele

Taslakları gözden geçirdiğiniz yer burası.

- Listede onaylayabileceğiniz bütün taslaklar, en yenisi en üstte ve notun kendi
  başlığıyla sıralanır. Kurulum dosyaları, kilitli notlar ve sohbet kaydı gibi ham
  kayıtlar bu listeye girmez. **Klasör** menüsünden yalnızca bir klasörün
  taslaklarını gösterebilirsiniz (Gelen kutusu, notes, log…).
- **Hepsini onayla** düğmesi, bir kez daha sorduktan sonra listedeki bütün taslakları
  onaylar. Yalnızca bazılarını onaylamak için kutucukları işaretleyip **Seçilenleri
  onayla**'ya basın.
- Bir taslağa tıklayınca sağda açılır. Metnin üstünde dört düğme var: **Onayla,
  sonrakine geç** (kısayolu ⌘↩), **Onayla**, **Atla** ve **Çöp kutusuna taşı**. Çöpe
  taşıdığınız notu Finder'daki Çöp Sepeti'nden geri alabilirsiniz.
- Onaylamak yalnızca `draft: true` işaretini kaldırır; notun metnine dokunulmaz.
- Bir asistan değişiklik önerisi gönderdiyse sayfanın üstünde turuncu bir satır
  çıkar. Tıklayınca önerilen metni ve mevcut hâlinden farkını görür, kabul eder ya da
  reddedersiniz.

### Notlar

Not klasörünüzün tamamını buradan okursunuz.

- **Sol taraf:** notları adına, klasörüne ya da özetine göre bulabilirsiniz. Filtreler:
  Kendi notlarım, Bu hafta değişenler, Gelen kutusu, Taslaklar, Aramaya dahil
  olmayanlar, Kurulum dosyaları ve şablonlar, Hepsi ve tek tek klasörler. Sıralama:
  son değişen ya da A–Z. Yanında **Yeni not** düğmesi.
- **Satırlardaki küçük işaretler:** turuncu nokta taslak demek; üstü çizili göz
  aramaya dahil değil demek; dönen oklar not değişti, dizine birazdan eklenecek demek;
  kilit ise not kilitli demek. Listenin yanındaki **?** bunları hatırlatır.
- **Sağ taraf:** notun bilgileri, metni, içindeki bağlantılar (Carry'nin içinde
  açılır) ve bu nota bağlantı veren diğer notlar. ⌘[ ve ⌘] ile geri ve ileri
  gidebilirsiniz. Düğmelerle notu Obsidian'da, varsayılan uygulamasında ya da
  Finder'da açarsınız.

### Notlarda ara

Buraya bir soru yazdığınızda Carry'nin asistanınıza tam olarak neyi vereceğini
görürsünüz. Bu sayfa bir sohbet cevabı yazmaz; yalnızca bulunan not bölümlerini
gösterir.

- **Zaman ifadeleri** sonuçları o tarihlerle sınırlar. "Geçen hafta ne yaptık" diye
  sorarsanız o haftanın notları, günlere ayrılmış ve en yenisi üstte olacak şekilde
  gelir. En üstteki özet, Carry'nin ifadeyi nasıl anladığını söyler ("“geçen hafta” =
  14–20 Eylül 2026") ve tek tıkla başka bir döneme geçmenizi sağlar.
- **Hem konu hem tarih** içeren sorularda ("geçen hafta fiyatlar hakkında ne karar
  verdik") önce o dönemin sonuçları gelir, ardından başka tarihlerdeki ilgili notlar
  **başka tarih** etiketiyle gösterilir. Çünkü bir olay bazen günler sonra yazıya
  geçer.
- **Tarih yoksa** özet, Carry'nin kaç bölüme baktığını ve kaçını elinde tuttuğunu
  söyler. Her sonucun yanında tarihi ve ne kadar iyi eşleştiği (güçlü, iyi, zayıf)
  yazar.

## Ayarlar

Ayarlar beş sekmeden oluşur: **Arama**, **Not klasörleri**, **Sohbet notları**,
**Asistanlar** ve **Genel**.

### Arama

"Carry nasıl arasın?" sorusu iki ayrı karara bölünmüştür. İkisini seçtiğinizde altta
**Seçiminiz** satırı birleşimi özetler; **Bunu kullan**'a basınca geçiş yapılır.
Anlamına göre arama seçildiyse gereken model indirilir ve notlar arka planda yeniden
taranır.

**1. Notları nasıl bulsun?**

| Seçenek | Ne kullanır | Açıklama |
|---|---|---|
| **Anlamına göre** (önerilen) | Ollama | Mac'inizde küçük bir model çalışır (embeddinggemma: 0,6 GB indirme, arama sırasında yaklaşık 0,7 GB bellek, boştayken bellekten çıkar). Aynı şeyi başka kelimelerle anlatan notları da bulur. |
| **Kelimelerine göre** | yerleşik | Sorudaki kelimeleri içeren notları bulur. Kurulacak bir şey yoktur. |

**2. Sonuçları kim kontrol etsin?** Kontrol adımı, bulunan bölümlerden yalnızca
soruyu gerçekten cevaplayanları bırakır; böylece asistan alakasız metinlerle kafa
karıştırmaz.

| Seçenek | Ne kullanır | Açıklama |
|---|---|---|
| **TypeSafe Jev** (önerilen) | çevrimiçi hizmet, erişim anahtarı | En isabetli ve en hızlı seçenek (yaklaşık yarım saniye). Soru ve en fazla 32 bölüm TypeSafe'e gider. |
| **Kendi asistanım (Claude ya da GPT)** | Claude Code'da Claude Haiku, Codex'te GPT | Ek bir hizmete ya da anahtara gerek yoktur. Carry asistana biraz daha fazla bölüm verir, hangilerinin işe yaradığına asistan karar verir: Claude Code'da bunu yardımcı bir adımda küçük bir Claude modeli, Codex'te Codex'in kendi GPT modeli yapar. Biraz daha yavaştır ve soru başına biraz daha fazla token harcar. |
| **Bu Mac'te büyük bir model** | 2 GB yerel model | Kontrol için hiçbir şey internete çıkmaz. Arama sırasında yaklaşık 3 GB boş bellek ister; 16 GB ve üzeri belleği olan Mac'ler içindir. Yalnızca "Anlamına göre" ile birlikte çalışır. |

Carry'nin 36 soruluk testinde *anlamına göre + Jev* doğru notu 36 sorunun 34'ünde,
*kelimelerine göre + Jev* ise 33'ünde ilk sıraya koydu.

**TypeSafe erişim anahtarı.** Jev'li bir seçenek seçiliyse görünür, değilse
katlanmış durur. console.typesafe.ai › API Keys'ten aldığınız anahtarı yapıştırıp
**Anahtarı kaydet**'e basın. Anahtar macOS Anahtar Zinciri'nde saklanır, Carry'nin
dosyalarına hiç yazılmaz. **Kontrol et** kayıtlı bir anahtar olup olmadığını söyler.

**Gelişmiş** (katlanmış durur; çoğu zaman dokunmanız gerekmez):

| Ayar | Varsayılan | Ne işe yarar? |
|---|---|---|
| Arama başına metin parçası | 8 | Asistanın bir soruda en fazla kaç bölüm alacağı. |
| Asistana gönderilecek en fazla metin | 10.000 karakter | Bu bölümlerin toplam uzunluğu için üst sınır. |
| Bir nottan en fazla parça | 2 | Tek bir uzun notun bütün yeri kaplamasını önler. |
| Jev alaka eşiği | 0,50 | Yalnızca Jev ile. Yükselttikçe daha az ama daha emin sonuç gelir. |
| Notlar değişince aramayı kendiliğinden güncelle | açık | Bir notu düzenlediğinizde Carry bunu kendisi fark eder. |
| Değişiklikleri kontrol etme sıklığı | 60 saniye | Carry'nin ne sıklıkla değişiklik aradığı. |
| GitHub klasörlerini eşitleme sıklığı | 5 dakika | GitHub'daki bilgi tabanları için (aşağıda anlatılıyor). |

Bir ayarı değiştirdiğinizde altta bir çubuk belirir: **Değişiklikleri kaydet**
(kısayolu ⌘S) ya da **Vazgeç**.

### Not klasörleri

- Carry'nin okuduğu her klasör için bir kart var. Kartta klasörün adı ve yeri,
  Carry'nin oraya kayıt ekleyip ekleyemeyeceği, **Notları göster** (klasörü Notlar
  sayfasında açar) ve **Carry'den çıkar** düğmeleri bulunur. Çıkarmak yalnızca
  Carry'nin o klasörde aramayı bırakması demektir; klasöre ve dosyalarına
  dokunulmaz. Klasörü başka bir yere taşıdıysanız **Klasörün yeni yerini seç** ile
  yeniden bağlarsınız.
- **Bu klasörün ayarları** (katlanmış):
  - *Carry buraya kendi kayıtlarını ekleyebilir (`carry/` alt klasörüne):* yalnızca
    değişiklik önerileri ve istem kaydı için gerekir.
  - *Bu klasörden TypeSafe'e metin gönderilmesine izin ver:* kapatırsanız bu
    klasördeki notlar Jev kontrolüne hiç gitmez, kontrol edilmeden döner.
  - *Aramaya dahil etme:* aramada görünmesini istemediğiniz klasörleri, dosyaları ya
    da desenleri virgülle ayırarak yazın (örneğin `+, x, workbench, *_index.md`). Bu
    dosyalar Notlar sayfasında görünmeye devam eder ama aramada hiç çıkmaz.
    **Kaydet**'e basınca notlar arka planda yeniden taranır.
  - *Aranabilir dosyaları listele:* aramaya gerçekte hangi dosyaların girdiğini
    gösterir.
- **Yeni not klasörü oluştur** kurulum rehberinin notlar adımını açar.
- **Not klasörü ekle** zaten var olan bir klasörü yalnızca okunacak şekilde ekler.
- **Gelişmiş kaynaklar** (katlanmış):
  - **GitHub bilgi tabanı:** GitHub'daki bir repoyu yalnızca okunacak şekilde
    ekler. Cihaz koduyla giriş yaparsınız, repoyu, dalı ve klasörü seçersiniz. Carry
    bir kopya tutar, beş dakikada bir güncellemelere bakar ve repoya hiçbir şey
    göndermez.
  - **Özel ayarlarla klasör ekle:** klasöre kendi kısa adınızı verir, Carry'nin
    oraya yazıp yazamayacağını seçersiniz.

### Sohbet notları

- **Taslakların dili:** Türkçe ya da English. Seçtiğiniz anda kaydedilir ve bütün
  sohbet notlarında kullanılır: akşam çalışmasında, elle başlattığınızda ve sohbet
  bittiğinde.
- **Her akşam:** Akşam çalışmasını açıp kapatır, saatini seçersiniz (varsayılan
  21:30). **Akşam planını kaydet**'e basmayı unutmayın. Çalışma, oturumunuz açıkken
  macOS'un kendi zamanlayıcısıyla (launchd) yapılır. Her Mac kullanıcısı için tek bir
  akşam çalışması olabilir; başka bir Carry kurulumu bunu kullanıyorsa Carry size
  söyler ve devralmadan önce sorar.
- **Bir sohbet bittiğinde:** Claude Code ve Codex'in bu not klasöründe biten bir
  sohbeti Carry'ye iletecek şekilde kurulu olup olmadığını gösterir. Carry'nin
  oluşturduğu klasörlerde bu baştan açıktır. Önceden sahip olduğunuz bir klasörde ise
  akşam çalışmasını açmanız yeterli. Codex bu kancayı ilk seferde bir kez onaylamanızı
  ister.
- **Son çalışmalar:** **Sohbetlerden şimdi not çıkar** hemen bir çalışma başlatır ve
  bitince sonucu söyler ("2 yeni taslak, İncele" ya da "yeni taslak yok"). Buradan
  kaydın tamamını ve Gelen kutusunu da açabilirsiniz; teknik ayrıntılar katlanmış
  bir bölümde durur.

### Asistanlar

- **Claude Code** ve **Codex** için birer kart vardır: kurulu mu, bağlantısı ayarlı
  mı?
  - **Bağla:** neyin değişeceğini sade bir dille anlatır (not klasörünüze tek bir
    ayar dosyası eklenir). Değişikliğin tamamını görmek isterseniz *Teknik
    ayrıntılar*'a bakın.
  - **Notlarımla başlat:** Terminal'i not klasörünüzde açar ve asistanı başlatır.
    macOS ilk seferde Carry'nin Terminal'i açmasına izin vermenizi ister.
  - Asistan kurulu değilse **Kurulum adımlarını aç** ve kurduktan sonra **Yeniden
    kontrol et** düğmeleri çıkar.
- **Gelişmiş** (katlanmış): başka bir proje klasörünü bağlayabilir, o projede
  yazdığınız mesajların kaydedilmesini açabilirsiniz. Bu kayıtlarda yalnızca sizin
  mesajlarınız olur, asistanın cevapları asla olmaz; her biri onay bekleyen bir taslak
  hâline gelir. Asistanın karar önerisi göndermesine izin verebilir, değişiklikleri
  uygulamadan önce görebilir, kaydı duraklatabilir ve daha önce yaptığınız herhangi
  bir bağlantıyı **Kurulumu geri al** ile kaldırabilirsiniz.

### Genel

- **Dil:** uygulamanın dili (Türkçe ya da English).
- **Yardım:** **Kurulum rehberini aç** (mevcut hiçbir şey silinmez), **Carry nasıl
  çalışır?** ve ana sayfadaki kullanım ipuçlarını geri getiren düğme.
- **Sorun giderme** (katlanmış):
  - *Carry'nin ayar klasörü:* nerede olduğu, **Finder'da göster** ve **Başka bir ayar
    klasörü aç**. Buradaki arama verileri her zaman yeniden oluşturulabilir; yine de
    klasörün kendisini silmeyin.
  - *Arama ve bağlantı durumu:* kullanılan arama altyapısı, **Not aramasını yeniden
    hazırla**, **Carry arama hizmetini kontrol et** (Carry'nin kendi MCP sunucusunu
    dener; asistanın verdiği izin asistanın içinden kontrol edilir) ve **Sağlayıcıyı
    yokla**.
  - *Tanılama:* bir sorun bildirmeniz gerekirse işe yarayacak, bütün durumun JSON
    hâli.

## Asistan Carry'yi nasıl kullanır?

Carry, Claude Code ve Codex'in not klasörünüz için başlattığı bir MCP sunucusudur
(`carry.mcp_server`). Asistana şu araçları verir:

- **`carry_recall(query, queries?, source_ids?, budget?)`:** soruyu cevaplayan
  bölümler. Her biri kaynağı, tarihi, kayıt kimliği ve revizyonuyla gelir; yanında
  arama durumunu anlatan makinece okunur bir özet vardır (dizin güncel mi, eksik bir
  şey var mı, hangi tarih aralığı kullanıldı). Sorudaki zaman ifadeleri korunmalıdır.
  Bütçe yalnızca düşürülebilir, artırılamaz.
- **`carry_catalog(folder?, source_ids?)`:** aranabilir bütün dosyalar ve tek
  satırlık özetleri. `carry_recall` bir şey bulamadığında işe yarar.
- **`carry_status()`:** kaynaklar, dizinin güncelliği ve eksikler. Asla not metni
  döndürmez.
- **`carry_propose`:** yalnızca o asistan için önerileri açtıysanız vardır. Taslak
  bir düzeltme oluşturur; onu kendisi asla kabul edemez.

Carry'nin döndürdüğü bölümler talimat değil, veridir. Carry asistandan kullandığı
bölümün kaynağını göstermesini ve kanıt yetersizse bunu açıkça söylemesini ister.

## Komut satırı

| Komut | Ne yapar? |
|---|---|
| `carry setup` | Rehberli kurulum ([Kurulum](#kurulum) bölümüne bakın). |
| `carry app install \| open \| remove` | `~/Applications/Carry.app`'i derler, açar ya da kaldırır. |
| `carry search --semantic on\|off [--judge jev\|assistant]` | Carry'nin nasıl bulup nasıl kontrol edeceğini değiştirir. |
| `carry recall "soru"` | Terminalden arama yapar. |
| `carry status [--probe]` | Neyin bağlı, neyin güncel, neyin eksik olduğunu gösterir. |
| `carry index` | Arama dizinini yeniden oluşturur. |
| `carry harvest [--dry-run] [--install-schedule \| --remove-schedule]` | Sohbet notlarını hemen çıkarır ya da akşam zamanlamasını kurar/kaldırır. |
| `carry context` | Yeni bir sohbetin başladığı durum özetini gösterir. |
| `carry connect claude\|codex <klasör>` · `list` · `undo` | Bir proje klasöründe asistanı bağlar (önizlenir, geri alınabilir). |
| `carry vault init <klasör>` | Şablondan yeni bir not klasörü oluşturur. |
| `carry github …` | Salt okunur GitHub bilgi kaynaklarını yönetir. |
| `carry proposal list \| show \| accept \| reject` | Değişiklik önerilerini terminalden yönetir. |

Birden fazla Carry kurulumu kullanıyorsanız komutlara `--workspace <ayar klasörü>`
ekleyin (ya da `CARRY_WORKSPACE` değişkenini ayarlayın). Makinece okunur çıktı için
`--json` kullanın.

## Geliştiriciler için

```sh
uv venv --python 3.13 .venv
.venv/bin/python -m pip install -e .        # çekirdeğin zorunlu bağımlılığı yok
.venv/bin/python -m unittest discover -s tests -q
```

İsteğe bağlı ekler: `.[embed]` (numpy ile daha hızlı vektör araması), `.[yaml]`
(PyYAML ile daha geniş frontmatter desteği) ve `.[rerank]` (yerel cross-encoder).
Çekirdek bunlar olmadan da çalışır, yalnızca neyin eksik olduğunu bildirir. Denemek
için `carry demo --into ~/carry-demo` küçük, uydurma bir not kümesi kurar.

Klasör yapısı:

- `src/carry/`: Python çekirdeği, MCP sunucusu, komut satırı, sohbet notları;
  tarih aralıkları `timeframe.py` içinde.
- `src/carry/app/CarryApp.swift`: SwiftUI uygulaması. `desktop.py`'deki JSON
  köprüsünün üzerinde çalışan bir kabuktur. Arayüz metinleri `L()`/`T()` üzerinden
  geçer; Türkçeleri `TR` tablosundadır.
- `src/carry/templates/vault/`: yeni not klasörü şablonu.
- `tests/`: testler.

Tasarım ilkeleri:

- **Asıl olan Markdown, dizin gözden çıkarılabilir.** Dizin yeniden oluşturulurken
  yalnızca ayar klasörüne yazılır. Yeni dizin tek adımda devreye girer; bir hata
  olursa eski dizin çalışmaya devam eder.
- **Klasör dışına çıkılmaz.** Her dosya yolu kendi kök klasörünün içinde çözülür;
  dışarı taşan yollar ve sembolik bağlantılar reddedilir.
- **Üzerine yazmak yok, geçmiş var.** Bir düzeltme yeni bir revizyon olarak yazılır,
  eskisi "yerini yenisi aldı" diye işaretlenir. Eskimiş bir revizyonun üzerine yazma
  denemesi reddedilir.
- **Eksik neyse söylenir.** Yalnızca kelimeyle arama, güncel olmayan dizin ya da
  ulaşılamayan bir sağlayıcı gizlenmez, açıkça bildirilir.

## Bilinen sınırlar

- Carry bir pilot: uygulama yerelde derleniyor ve ad-hoc imzalı. İmzalı, notarize
  edilmiş bir sürüm henüz yok; temiz ikinci bir Mac'e kurulum hâlâ doğrulanıyor.
- Gizli bilgi maskeleme elden geldiğince yapılır; hiç görülmemiş bir anahtar biçimi
  gözden kaçabilir.
- Zaman ifadeleri için tarihler dosya adındaki tarihten, tarihli başlıklardan ve
  created/date/updated alanlarından okunur. Bunların hiçbiri olmayan bir notun tarihi
  bilinmez.
- Bir konuyu tarih aralığı içinde ararken, aynı kökten gelen kelimeler gevşek
  eşleşebilir (örneğin "carry" araması "carrying" geçen notları da getirebilir).
- Kaynaklara verilen kapsam etiketleri yalnızca kökeni gösterir, erişim denetimi
  değildir. Çok kullanıcılı yetkilendirme kapsam dışındadır.

<sub>**Berke Tevik** tarafından geliştirildi.</sub>

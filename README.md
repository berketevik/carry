# Carry

> 🇹🇷 **Türkçe:** Bu belgenin Türkçesi aşağıda: [Türkçe sürüme git](#carry-türkçe)

**Your notes, as your AI assistant's memory.**

Carry connects the Markdown notes on your Mac to the AI assistants you already
use, Claude Code and Codex. When you ask your assistant something, it first looks
up the related parts of your notes through Carry, with citations. When a chat
ends, Carry picks out what was decided and what is still open and saves it as a
draft note for you to check. Your notes stay ordinary text files, in a folder you
choose, on your Mac.

Version 0.6.0 · macOS 14 or later · internal pilot (not yet Developer ID signed)

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

- Internal pilot: the app is ad-hoc signed and built locally; there is no signed,
  notarised release, and installation on a second, clean Mac is still being
  verified.
- Secret masking is best effort; a new token format can slip through.
- Dates for time words come from dated file names, dated headings and
  created/date/updated fields. A note without any of them has no date.
- Words that share a stem can match loosely when the time-range fallback lists
  notes by topic (for example "carry" also matches "carrying").
- Source scope labels are for attribution, not access control; multi-user
  permissions are out of scope.

---
---

# Carry (Türkçe)

**Notlarınız, yapay zekâ asistanınızın hafızası.**

Carry, Mac'inizdeki Markdown notlarını zaten kullandığınız yapay zekâ asistanlarına,
yani Claude Code ve Codex'e bağlar. Asistanınıza bir şey sorduğunuzda, önce Carry
üzerinden notlarınızın ilgili bölümlerine kaynak göstererek bakar. Bir sohbet
bittiğinde Carry neyin kararlaştırıldığını ve neyin açık kaldığını seçip kontrol
etmeniz için taslak not olarak kaydeder. Notlarınız sıradan metin dosyaları olarak,
seçtiğiniz bir klasörde, Mac'inizde kalır.

Sürüm 0.6.0 · macOS 14 ve üzeri · şirket içi pilot (henüz Developer ID imzalı değil)

## İçindekiler

- [Neden Carry](#neden-carry)
- [Nasıl çalışır](#nasıl-çalışır)
- [Temel kavramlar](#temel-kavramlar)
- [Gizlilik: ne kalır, ne gider](#gizlilik-ne-kalır-ne-gider)
- [Kurulum](#kurulum)
- [Uygulamanın kullanımı](#uygulamanın-kullanımı)
- [Ayarlar, tek tek](#ayarlar-tek-tek)
- [Asistanlar Carry'yi nasıl kullanır](#asistanlar-carryyi-nasıl-kullanır)
- [Komut satırı](#komut-satırı)
- [Geliştiriciler için](#geliştiriciler-için)
- [Bilinen sınırlar](#bilinen-sınırlar)

## Neden Carry

Yapay zekâ asistanları unutur. Her yeni sohbet sıfırdan başlar: projeyi yeniden
anlatırsınız, geçen hafta aldığınız kararı tekrarlarsınız, dünkü oturumun sonunda
çıkan yapılacaklar kaybolur. Carry bunu üç şeyle çözer:

1. **Asistanınız notlarınızı kullanır.** Claude Code ya da Codex cevap vermeden önce
   notlarınızda arar ve soruyu yanıtlayan bölümleri, geldikleri notu göstererek alır.
2. **Sohbetler nota dönüşür.** Bir sohbet bittiğinde Carry onu okur; kararları,
   bilgileri, tercihleri ve yarım kalan işleri çıkarıp Gelen kutunuza taslak olarak
   kaydeder. Her madde sohbetten birebir bir alıntı taşır; hiçbir şey uydurulmaz.
3. **Kontrol sizdedir.** Asistanın yazdığı hiçbir şey kendiliğinden kesin bilgi
   olmaz. Taslaklar siz onaylayana kadar bekler; bir sonraki sohbet de hâlâ açık olan
   işlerin kısa bir özetiyle başlar.

## Nasıl çalışır

```
 Not klasörünüz (Markdown)             Mac'inizde Carry                   Asistanınız
 ┌─────────────────────────┐   okur    ┌──────────────────────────┐  MCP  ┌───────────────┐
 │ notes/  log/  sources/  │ ────────▶ │ arama dizini (silinebilir)│◀────▶│ Claude Code   │
 │ + (Gelen kutusu)        │           │ carry_recall / catalog   │       │ Codex         │
 └─────────────────────────┘           └──────────────────────────┘       └───────┬───────┘
            ▲                                                                     │ sohbet biter
            │   taslak notlar (onayınız için)      ┌──────────────────────────┐   │
            └───────────────────────────────────── │ harvest: karar, yapılacak│ ◀─┘
                                                   └──────────────────────────┘
```

- **Doğruluk kaynağı notlardır.** Carry yalnızca yanlarında bir arama dizini
  oluşturur. Dizin istenildiği zaman silinip yeniden oluşturulabilir; hiçbir not
  kaybolmaz.
- **Arama** iki adımda çalışır: aday bölümleri *bulmak* (kelimeyle ya da küçük bir
  yerel modelle anlamına göre) ve hangilerinin soruyu gerçekten yanıtladığını
  *kontrol etmek* (TypeSafe Jev, kendi asistanınız ya da büyük bir yerel model).
- **Zaman ifadeleri çalışır.** "Geçen hafta ne yaptık?", "dün", "son 7 gün",
  "Eylül'de" gibi ifadeler aramayı o döneme tarihlenmiş notlarla sınırlar.
- **Harvest** bir sohbet bittiğinde (isterseniz her akşam da) çalışır ve her sohbet
  için Gelen kutusuna bir taslak özet yazar.

## Temel kavramlar

| Kavram | Anlamı |
|---|---|
| **Not klasörü** | Notlarınızın `.md` dosyaları olarak durduğu klasör. Obsidian gibi uygulamalar buna *vault* der. Carry onu okur, asla yeniden düzenlemez. |
| **Asistan** | Konuştuğunuz yapay zekâ aracı: Claude Code ya da Codex. Bağlandığında notlarınızda arama yapabilir. |
| **Arama dizini** | Carry'nin doğru notu hızlıca bulmak için notlarınızdan hazırladığı bilgiler. Kendiliğinden güncellenir; her zaman yeniden oluşturulabilir. |
| **Sohbet notları (harvest)** | Carry'nin biten sohbetlerinizden yazdığı taslaklar: kararlar, bilgiler, tercihler, yarım kalan işler. |
| **Gelen kutusu** | Not klasörünüzdeki `+` klasörü; yeni taslaklar buraya düşer. |
| **Taslak** | Henüz kontrol etmediğiniz not (`draft: true`). Onaylamak bu işareti kaldırır; metin değişmez. |
| **Değişiklik önerisi** | Asistanın kendisinin değiştiremediği bir not için önerdiği düzeltme. Kabul eder ya da reddedersiniz. |
| **Ayar klasörü** | Carry'nin ayarlarını ve arama dizinini tuttuğu yer (varsayılan `~/Library/Application Support/Carry`). Notlarınız asla burada saklanmaz. |

## Gizlilik: ne kalır, ne gider

- **Mac'inizde kalır:** notlarınız, arama dizini, ayarlar ve "anlamına göre bulma"
  modeli (Ollama yerelde çalışır).
- **Kullandığınız asistana gider:** Carry'nin bir soru için döndürdüğü bölümler;
  tıpkı onları kendiniz yapıştırmışsınız gibi.
- **TypeSafe'e gider (yalnızca Jev kontrolünü seçerseniz):** soru ve en fazla 32
  bulunan bölüm; gizli bilgiler elden geldiğince maskelenir. TypeSafe bunlarla model
  eğitmediğini belirtiyor. Bir klasör bundan klasör bazında hariç tutulabilir;
  `sensitivity: secret` işaretli notlar asla gönderilmez.
- Carry analiz verisi göndermez.

## Kurulum

### Gerekenler

- macOS 14 ve üzeri.
- [Claude Code](https://docs.anthropic.com/en/docs/claude-code/overview) ve/veya
  [Codex](https://github.com/openai/codex): en az biri.
- Carry'yi kurmak için [uv](https://docs.astral.sh/uv/) (kendi Python'unu getirir).
- macOS uygulamasını derlemek için Xcode Command Line Tools (`xcode-select --install`).
- İsteğe bağlı: anlamına göre arama için [Ollama](https://ollama.com) (yoksa kurulum
  Homebrew ile kurar) ve Jev kontrolü için bir TypeSafe erişim anahtarı.

### Adımlar

```sh
gh auth login && gh auth setup-git                      # repo şimdilik özel
uv tool install "git+https://github.com/<owner>/carry"   # size verilen repo
carry setup
```

`carry setup` kısa bir terminal sihirbazıdır. Ayar klasörünü oluşturur, şablondan
yeni bir not klasörü açar (ya da mevcut klasörünüzü kullanır), anlamına göre aramayı
açar, Claude Code ve Codex'i bağlar, macOS uygulamasını `~/Applications/Carry.app`
olarak derler ve notlarınızı indeksler. `carry setup --yes --vault ~/Notlar` her
varsayılanı kabul eder. `--no-semantic` Mac'te hiç model çalıştırmaz, `--no-app`
uygulamayı atlar.

Tıklayarak kurmayı mı tercih edersiniz? `carry app install` çalıştırın,
`~/Applications`'tan **Carry**'yi açın; uygulamanın kurulum rehberi aynı adımları
yapar.

Güncellemek için `uv tool upgrade carry`, ardından uygulamayı yeni sürüme göre
yeniden derlemek için `carry app install`.

## Uygulamanın kullanımı

Kenar çubuğunda beş sayfa vardır: **Ana sayfa · İncele · Notlar · Notlarda ara ·
Ayarlar**. En altta **Carry nasıl çalışır?** (kısa rehber ve sözlük) bulunur.
Arayüz Türkçe ya da İngilizcedir (Ayarlar › Genel).

### İlk açılış: kurulum rehberi

1. **Tanışma.** Carry'nin ne yaptığı, üç maddede. Carry'nin Claude Code ve Codex
   ile çalıştığını, Claude'un ya da ChatGPT'nin normal sohbet uygulamalarıyla
   çalışmadığını açıkça söyler.
2. **Notlarınız.** İki seçenek:
   - *Yeni bir not klasörü oluştur*: Carry, asistanınız için kısa bir kılavuz ve
     düzenli alt klasörlerle bir klasör hazırlar (varsayılan
     `~/Documents/Carry Notları`). Sohbet notlarının dilini de burada seçersiniz.
   - *Bilgisayarımda .md not dosyalarım var*: klasörü seçin (örneğin bir Obsidian
     vault'u). Carry içinde gerçekten `.md` dosyası olup olmadığını kontrol eder ve
     yalnızca okur.
3. **Asistan.** Her asistan için bir kart: kurulu mu, bağlı mı. **Bağla**, not
   klasörünüze küçük bir ayar dosyası ekler (önce değişikliğin tamamını
   görebilirsiniz). Tek asistan yeterlidir.
4. **Hazır.** Klasör boşsa **İlk notumu yaz**; ayrıca Terminal'i not klasörünüzde
   açıp asistanı başlatan **Claude Code'u / Codex'u başlat**. İlk seferde asistan
   Carry'yi kullanmak için izin ister: izin verin.

Rehberi istediğiniz zaman Ayarlar › Genel'den yeniden açabilirsiniz.

### Ana sayfa

- **Durum satırı.** Her şey çalışıyorsa tek cümle ("Her şey hazır. Asistanınız 541
  notunuzda arama yapabiliyor.") ve tam kontrol listesi için **Ayrıntılar**: not
  klasörü, arama, her asistan, sohbet notları. Yapılması gereken bir şey varsa liste
  kendiliğinden açılır ve eksik adımın yanında bir düğme olur.
- **Üç kart.** **İncele** (taslak bekliyorsa vurgulu), **Notlarda ara**, **Yeni not**.
- **Sohbetlerinizden yarım kalan işler** ve **son kararlar**: son sohbet
  notlarından alınır, her birinin nereden geldiğine bağlantısı vardır.
- **Son değişen** notlar.

### İncele

Taslakları gözden geçirmenin tek yeri.

- Liste onaylanabilecek tüm taslakları en yeniden eskiye, notun kendi başlığıyla
  gösterir (kurulum dosyaları, kilitli notlar ve sohbet kaydı gibi ham kayıtlar
  dışarıda kalır). **Klasör** menüsü listeyi daraltır (Gelen kutusu, notes, log…).
- **N taslağın hepsini onayla…** görünenlerin hepsini, bir onay penceresinden sonra
  onaylar. Bir kısmı için kutuları işaretleyip **Seçilenleri onayla**.
- Sağda okumak için bir taslağa tıklayın. Metnin üstünde: **Onayla, sonrakine geç**
  (⌘↩), **Onayla**, **Atla**, **Çöp kutusuna taşı…** (Finder'daki Çöp Sepeti'nden
  geri alınabilir).
- Onaylamak yalnızca `draft: true` satırını kaldırır; metin asla değişmez.
- Bir asistan değişiklik önerisi göndermişse, en üstteki turuncu satır **Değişiklik
  önerileri** sayfasını açar. Orada önerilen metni ve mevcut nottan farkını görür,
  kabul eder ya da reddedersiniz.

### Notlar

Not klasörünüzün tamamı için bir okuyucu.

- Solda: ada, klasöre ya da özete göre bulma; bir filtre (Kendi notlarım, Bu hafta
  değişenler, Gelen kutusu, Taslaklar, Aramaya dahil olmayanlar, Kurulum dosyaları ve
  şablonlar, Hepsi, tek bir klasör); son değişene ya da A–Z'ye göre sıralama;
  **Yeni not**.
- Her satırdaki küçük işaretler: turuncu nokta = taslak, çizili göz = aramaya dahil
  değil, dönen oklar = değişti ve dizine eklenmeyi bekliyor, kilit = kilitli.
  Listenin yanındaki **?** bunları açıklar.
- Sağda: notun özellikleri, metni, bağlantıları (Carry içinde açılır) ve ona
  bağlantı veren notlar. ⌘[ ve ⌘] ile geri/ileri. Düğmeler notu Obsidian'da,
  varsayılan uygulamasında ya da Finder'da açar.

### Notlarda ara

Bir soru yazın ve Carry'nin asistanınıza tam olarak ne vereceğini görün. Burada
sohbet yanıtı oluşturulmaz.

- **Zaman ifadeleri** sonuçları bir döneme sınırlar: "geçen hafta ne yaptık" o
  haftanın notlarını, güne göre gruplanmış ve en yeniden eskiye gösterir. Özet
  satırı Carry'nin ifadeyi nasıl anladığını söyler ("“geçen hafta” = 14–20 Eylül
  2026") ve tek tıkla başka dönemler sunar.
- Hem konu hem zaman içeren bir soru ("geçen hafta fiyatlandırma hakkında ne karar
  verdik") önce o dönemin sonuçlarını, ardından diğer tarihlerden ilgili notları
  **başka tarih** etiketiyle gösterir: olay daha sonra yazıya geçirilmiş olabilir.
- Zaman yoksa özet, Carry'nin kaç bölüme baktığını ve kaçını tuttuğunu söyler. Her
  sonuç tarihini ve bir eşleşme etiketini (güçlü, iyi, zayıf) gösterir.

## Ayarlar, tek tek

Ayarlarda beş sekme vardır: **Arama · Not klasörleri · Sohbet notları · Asistanlar ·
Genel**.

### Arama

**Carry nasıl arasın?** iki sorudan oluşur. Seçtiğiniz birleşim **Seçiminiz**
altında görünür; **Bunu kullan** ona geçer (anlamına göre arama modelini indirir ve
arka planda yeniden indeksler).

**1. Notları nasıl bulsun?**

| Seçenek | Kullanır | Ne yapar |
|---|---|---|
| **Anlamına göre** (önerilen) | Ollama | Mac'inizde çalışan küçük bir model (embeddinggemma: 0,6 GB indirme, arama sırasında yaklaşık 0,7 GB bellek, boştayken bellekten çıkar) başka kelimelerle yazılmış notları da bulur. |
| **Kelimelerine göre** | yerleşik | Sorudaki kelimeleri içeren notları bulur. Kurulacak bir şey yok. |

**2. Sonuçları kim kontrol etsin?** Kontrol, yalnızca soruyu gerçekten yanıtlayan
bölümleri tutar; böylece asistan gevşek bağlantılı metinlerle yanılmaz.

| Seçenek | Kullanır | Ne yapar |
|---|---|---|
| **TypeSafe Jev** (önerilen) | çevrimiçi hizmet, erişim anahtarı | En isabetli ve en hızlı (yaklaşık yarım saniye). Soru ve en fazla 32 bölüm TypeSafe'e gider. |
| **Kendi asistanım (Claude ya da GPT)** | Claude Code: Claude Haiku · Codex: GPT | Ek hizmet ya da anahtar yok. Carry biraz daha fazla bölüm verir; Claude Code'da bunları yardımcı bir adımda küçük bir Claude modeli, Codex'te Codex'in kendi GPT modeli ayıklar. Biraz daha yavaş, soru başına biraz daha fazla token. |
| **Bu Mac'te büyük bir model** | 2 GB yerel model | Kontrol için hiçbir şey çevrimiçine gitmez. Arama sırasında yaklaşık 3 GB boş bellek ister; 16 GB ve üzeri Mac'ler için. Yalnızca "Anlamına göre" ile çalışır. |

Carry'nin 36 soruluk değerlendirmesinde *anlamına göre + Jev* doğru notu 34 kez,
*kelimelerine göre + Jev* 33 kez ilk sıraya koydu.

**TypeSafe erişim anahtarı.** Bir Jev seçeneği seçiliyken görünür (değilse
katlıdır). console.typesafe.ai › API Keys'ten aldığınız anahtarı yapıştırıp
**Anahtarı kaydet**'e basın; anahtar macOS Anahtar Zinciri'nde saklanır, asla
Carry'nin dosyalarında değil. **Kontrol et** anahtar kayıtlı mı söyler.

**Gelişmiş** (katlı):

| Ayar | Varsayılan | Anlamı |
|---|---|---|
| Arama başına metin parçası | 8 | Asistanın en fazla kaç bölüm alacağı. |
| Asistana gönderilecek en fazla metin | 10.000 karakter | Bu bölümlerin toplam uzunluğunun üst sınırı. |
| Bir nottan en fazla parça | 2 | Tek bir uzun notun bütün yerleri doldurmasını önler. |
| Jev alaka eşiği | 0,50 | Yalnızca Jev ile. Yüksek değer daha az ama daha emin bölüm döndürür. |
| Notlar değişince aramayı kendiliğinden güncelle | açık | Carry değişiklikleri kendisi fark eder. |
| Değişiklikleri kontrol etme sıklığı | 60 sn | Ne sıklıkla baktığı. |
| GitHub klasörlerini eşitleme sıklığı | 5 dk | GitHub bilgi tabanları için (aşağıya bakın). |

Bir şey değiştiğinde altta bir çubuk belirir: **Değişiklikleri kaydet** (⌘S) ya da
**Vazgeç**.

### Not klasörleri

- Her klasör için bir kart: adı ve yolu, Carry'nin oraya kayıt ekleyip
  ekleyemeyeceği, **Notları göster** (Notlar sayfasında açar) ve **Carry'den çıkar…**
  (Carry o klasörde aramayı bırakır; klasöre ve dosyalarına dokunulmaz). Klasör
  taşındıysa **Klasörün yeni yerini seç…** yeniden bağlar.
- **Bu klasörün ayarları** (katlı):
  - *Carry buraya kendi kayıtlarını ekleyebilir (`carry/` alt klasörüne)*: yalnızca
    değişiklik önerileri ve istem kaydı için gerekir.
  - *Bu klasörden TypeSafe'e metin gönderilmesine izin ver*: bu klasörün
    bölümlerini Jev kontrolünden uzak tutmak için kapatın; o zaman kontrol
    edilmeden döndürülür.
  - *Aramaya dahil etme*: virgülle ayrılmış klasörler, dosyalar ya da desenler
    (örneğin `+, x, workbench, *_index.md`). Notlar sayfasında görünmeye devam
    ederler ama aramada asla dönmezler. **Kaydet** arka planda yeniden indeksler.
  - *Aranabilir dosyaları listele* gerçekte neyin indekslendiğini gösterir.
- **Yeni not klasörü oluştur…** kurulum rehberinin notlar adımını açar.
- **Not klasörü ekle…** mevcut bir klasörü salt okunur olarak ekler.
- **Gelişmiş kaynaklar** (katlı): salt okunur bir **GitHub bilgi tabanı** (cihaz
  koduyla giriş, repo, dal ve klasör seçimi; Carry bir kopya tutar, beş dakikada bir
  güncellemeleri kontrol eder ve asla push etmez) ve **Özel ayarlarla klasör ekle**
  (kendi kısa adınız, yazılabilir ya da değil).

### Sohbet notları

- **Taslaklar › Taslakların dili**: Türkçe ya da English; hemen kaydedilir ve tüm
  harvest çalışmalarında kullanılır: gecelik, elle başlatılan ve sohbet sonu.
- **Her akşam**: akşam çalışmasını açıp kapatın, saati seçin (varsayılan 21:30) ve
  **Akşam planını kaydet**. Oturumunuz açıkken macOS launchd üzerinden çalışır. Her
  Mac kullanıcısı için tek bir akşam çalışması vardır; başka bir Carry kurulumuna
  aitse Carry bunu söyler ve taşımadan önce sorar.
- **Bir sohbet bittiğinde**: Claude Code ve Codex'in bu not klasöründe biten bir
  sohbeti Carry'ye verecek şekilde kurulu olup olmadığını gösterir. Carry'nin
  oluşturduğu klasörlerde bu baştan açıktır; önceden sahip olduğunuz bir klasör için
  bunun yerine akşam çalışmasını açın. Codex kancasını bir kez onaylamanızı ister.
- **Son çalışmalar**: **Sohbetlerden şimdi not çıkar** bir çalışma başlatır ve
  sonucunu bildirir ("2 yeni taslak, İncele" ya da "yeni taslak yok"). **Kaydın
  tamamını aç**, **Gelen kutusunu göster** ve katlı bir teknik kayıt.

### Asistanlar

- **Claude Code** ve **Codex** için birer kart: kurulu mu, bağlantı ayarlı mı.
  - **Bağla**, neyin değişeceğini sade bir dille gösterir (not klasörünüzde tek bir
    ayar dosyası); değişikliğin tamamı *Teknik ayrıntılar* altındadır.
  - **Notlarımla başlat**, Terminal'i not klasörünüzde açıp asistanı başlatır.
    macOS, Carry'nin Terminal'i kontrol etmesi için bir kez izin ister.
  - Asistan kurulu değilse: **Kurulum adımlarını aç** ve **Yeniden kontrol et**.
- **Gelişmiş** (katlı): başka bir proje klasörünü bağlama; o projede *kullanıcı
  istemlerinin tamamını kaydetme* (yalnızca sizin istemleriniz, asla yanıtlar; her
  biri onaylanmamış bir taslak olur; maskeleme elden geldiğince yapılır); *bu
  istemcinin taslak karar önermesine izin verme*; değişiklikleri önizleme, uygulama,
  kaydı duraklatma ve önceki herhangi bir bağlantı için **Kurulumu geri al**.

### Genel

- **Dil**: uygulamanın arayüz dili (Türkçe / English).
- **Yardım**: **Kurulum rehberini aç** (sahip olduğunuz hiçbir şey silinmez),
  **Carry nasıl çalışır?** ve **Ana sayfadaki kullanım ipuçlarını yeniden göster**.
- **Sorun giderme** (katlı):
  - *Carry'nin ayar klasörü*: yolu, **Finder'da göster**, **Başka bir ayar klasörü
    aç**. Buradaki arama verileri her zaman yeniden oluşturulabilir; klasörün
    kendisini silmeyin.
  - *Arama ve bağlantı durumu*: kullanılan arama altyapısı; **Not aramasını yeniden
    hazırla**; **Carry arama hizmetini kontrol et** (Carry'nin kendi MCP sunucusunu
    test eder; asistanın izni asistanın kendisinde kontrol edilir); **Sağlayıcıyı
    yokla**.
  - *Tanılama*: hata bildirimleri için tüm durumun JSON hâli.

## Asistanlar Carry'yi nasıl kullanır

Carry, Claude Code ve Codex'in not klasörünüz için başlattığı bir MCP sunucusudur
(`carry.mcp_server`). Sunduğu araçlar:

- **`carry_recall(query, queries?, source_ids?, budget?)`**: soruyu yanıtlayan
  bölümler; her biri kaynak, tarih, kayıt kimliği ve revizyonla, ayrıca makinece
  okunabilir bir arama durumu (dizin tazeliği, eksik yetenekler, kullanılan tarih
  aralığı). Sorgudaki zaman ifadelerini koruyun; bütçe yalnızca düşürülebilir, asla
  artırılamaz.
- **`carry_catalog(folder?, source_ids?)`**: aranabilir her dosya ve tek satırlık
  özeti; recall bir şey bulamadığında kullanılır.
- **`carry_status()`**: kaynaklar, dizin tazeliği ve eksik yetenekler; asla not
  metni içermez.
- **`carry_propose`**: yalnızca o istemci için önerileri açtıysanız; taslak bir
  düzeltme oluşturur ve onu asla kabul edemez.

Dönen bölümler talimat değil veridir; yanıt, asistandan kullandığı şeyi kaynak
göstermesini ve kanıt yetersizse bunu söylemesini ister.

## Komut satırı

| Komut | Ne yapar |
|---|---|
| `carry setup` | Rehberli kurulum ([Kurulum](#kurulum) bölümüne bakın). |
| `carry app install \| open \| remove` | `~/Applications/Carry.app`'i derler, açar ya da kaldırır. |
| `carry search --semantic on\|off [--judge jev\|assistant]` | Carry'nin nasıl bulup kontrol ettiğini değiştirir. |
| `carry recall "soru"` | Terminalden arama. |
| `carry status [--probe]` | Neyin bağlı, güncel ya da eksik olduğu. |
| `carry index` | Arama dizinini yeniden oluşturur. |
| `carry harvest [--dry-run] [--install-schedule \| --remove-schedule]` | Hemen sohbet notları ya da akşam zamanlaması. |
| `carry context` | Yeni bir sohbetin başladığı durum özeti. |
| `carry connect claude\|codex <klasör>` · `list` · `undo` | Bir proje klasöründe asistan bağlama (önizlemeli, geri alınabilir). |
| `carry vault init <klasör>` | Şablondan not klasörü oluşturur. |
| `carry github …` | Salt okunur GitHub bilgi kaynakları. |
| `carry proposal list \| show \| accept \| reject` | Terminalden değişiklik önerileri. |

Birden fazla kurulum kullanıyorsanız `--workspace <ayar klasörü>` ekleyin (ya da
`CARRY_WORKSPACE` ayarlayın); makinece okunabilir çıktı için `--json`.

## Geliştiriciler için

```sh
uv venv --python 3.13 .venv
.venv/bin/python -m pip install -e .        # çekirdeğin zorunlu bağımlılığı yok
.venv/bin/python -m unittest discover -s tests -q
```

İsteğe bağlı ekler: `.[embed]` (numpy, daha hızlı vektör araması), `.[yaml]`
(PyYAML, daha geniş frontmatter desteği), `.[rerank]` (yerel cross-encoder).
Çekirdek bunlar olmadan da çalışır ve eksikliği bildirir. `carry demo --into
~/carry-demo` denemek için küçük bir sentetik not kümesi kurar.

Yapı: `src/carry/` (Python çekirdeği, MCP sunucusu, CLI, harvest, zaman aralıkları
`timeframe.py`'de), `src/carry/app/CarryApp.swift` (SwiftUI uygulaması;
`desktop.py`'deki JSON köprüsünün üzerinde bir kabuk; arayüz metinleri `L()`/`T()`
üzerinden geçer, Türkçesi `TR` tablosundadır), `src/carry/templates/vault/` (not
klasörü şablonu), `tests/`.

Tasarım kuralları:

- **Markdown asıl, dizin silinebilir.** Yeniden oluşturma yalnızca ayar klasörünün
  içine yazar; yeni dizin tek adımda yayımlanır, bu yüzden başarısız bir oluşturma
  eski dizini çalışır bırakır.
- **Sınırlar içinde kalma.** Her yol kendi kök klasörünün içinde çözülür; dışarı
  çıkan yollar ve sembolik bağlantılar reddedilir.
- **Üzerine yazmak yerine geçmiş.** Bir düzeltme yeni bir revizyon yazar ve eskisini
  "yerini aldı" olarak işaretler; eski bir revizyona karşı yazma reddedilir.
- **Dürüst eksiklik bildirimi.** Yalnızca kelimeyle arama, eskimiş bir dizin ya da
  erişilemeyen bir sağlayıcı bildirilir, asla gizlenmez.

## Bilinen sınırlar

- Şirket içi pilot: uygulama ad-hoc imzalı ve yerelde derleniyor; imzalı ve
  notarize edilmiş bir sürüm yok, ikinci ve temiz bir Mac'e kurulum hâlâ
  doğrulanıyor.
- Gizli bilgi maskeleme elden geldiğince yapılır; yeni bir anahtar biçimi gözden
  kaçabilir.
- Zaman ifadelerinin tarihleri, dosya adlarındaki tarihlerden, tarihli
  başlıklardan ve created/date/updated alanlarından gelir. Bunların hiçbiri olmayan
  bir notun tarihi yoktur.
- Tarih aralığı yedek listesi notları konuya göre sıralarken aynı kökü paylaşan
  kelimeler gevşek eşleşebilir (örneğin "carry", "carrying" ile de eşleşir).
- Kaynak kapsam etiketleri erişim denetimi değil, yalnızca köken bilgisidir; çok
  kullanıcılı izinler kapsam dışıdır.

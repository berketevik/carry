"""`carry setup`: a terminal wizard that creates the workspace, a vault or source,
the search setting and client wiring, builds the index and says so in large letters.

Standard library only. Every step calls the same functions as the CLI and the app;
the wizard adds prompts, a spinner and the final banner. --yes takes every default.
"""
import getpass
import itertools
import os
from pathlib import Path
import shutil
import sys
import threading
import time

from . import github, index, jev, models, vault as vault_module
from .config import EmbeddingConfig, RetrievalConfig, SourceConfig, Workspace, CONFIG_NAME
from .errors import CarryError

TTY = sys.stdout.isatty() and os.environ.get('TERM') != 'dumb'
COLOR = TTY and not os.environ.get('NO_COLOR')


def paint(text, code):
    return f'\033[{code}m{text}\033[0m' if COLOR else text


BOLD, DIM, CYAN, GREEN, YELLOW, RED, MAGENTA = '1', '2', '36', '32', '33', '31', '35'


def say(text=''):
    print(text, flush=True)


def step(number, total, title):
    say()
    say(paint(f'[{number}/{total}] ', DIM) + paint(title, BOLD))


def ok(text):
    say(paint('  ✓ ', GREEN) + text)


def warn(text):
    say(paint('  ! ', YELLOW) + text)


class Prompter:
    def __init__(self, assume_yes=False, stream=None):
        self.assume_yes = assume_yes
        self.stream = stream

    def _input(self, prompt):
        if self.stream is not None:
            line = self.stream.readline()
            print(prompt + line.rstrip('\n'))
            return line.rstrip('\n')
        return input(prompt)

    def ask(self, question, default=''):
        if self.assume_yes:
            say(f'  {question} {paint(default or "—", DIM)}')
            return default
        answer = self._input(f'  {question} ' + paint(f'[{default}] ' if default else '', DIM)).strip()
        return answer or default

    def choose(self, question, options, default=1):
        say(f'  {question}')
        for i, (_, label) in enumerate(options, 1):
            marker = paint('›', CYAN) if i == default else ' '
            say(f'   {marker} {i}. {label}')
        while True:
            answer = self.ask('Seçim:', str(default))
            if answer.isdigit() and 1 <= int(answer) <= len(options):
                return options[int(answer) - 1][0]
            warn('1 ile %d arasında bir sayı yazın.' % len(options))

    def yes(self, question, default=True):
        answer = self.ask(question + (' (E/h)' if default else ' (e/H)'), 'e' if default else 'h').lower()
        return answer.startswith(('e', 'y')) if answer else default

    def secret(self, question):
        if self.assume_yes:
            return ''
        if self.stream is not None:
            return self.stream.readline().strip()
        return getpass.getpass(f'  {question} ').strip()


class Spinner:
    """Shows progress for a blocking call; plain text when not a terminal."""
    def __init__(self, label):
        self.label, self.done = label, threading.Event()

    def __enter__(self):
        if TTY:
            def spin():
                for frame in itertools.cycle('⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'):
                    if self.done.wait(0.08):
                        break
                    sys.stdout.write(f'\r  {paint(frame, CYAN)} {self.label}')
                    sys.stdout.flush()
            self.thread = threading.Thread(target=spin, daemon=True)
            self.thread.start()
        else:
            say(f'  … {self.label}')
        return self

    def __exit__(self, *exc):
        self.done.set()
        if TTY:
            self.thread.join()
            sys.stdout.write('\r' + ' ' * (len(self.label) + 6) + '\r')
            sys.stdout.flush()
        return False


def icloud_synced(path):
    """Desktop & Documents sync turns ~/Documents into iCloud storage: a venv or index there
    gets evicted or duplicated."""
    home = Path.home()
    synced = (home / 'Library/Mobile Documents/com~apple~CloudDocs/Documents').exists()
    path = Path(path).expanduser().resolve()
    inside = any(path == root or root in path.parents
                 for root in (home / 'Documents', home / 'Desktop', home / 'Library/Mobile Documents'))
    return inside and (synced or 'Mobile Documents' in str(path))


def onedrive_synced(path):
    """OneDrive's folder backup does the same to Documents and Desktop on Windows."""
    path = Path(path).expanduser().resolve()
    roots = {Path(os.environ[k]).resolve() for k in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial')
             if os.environ.get(k)}
    return any(path == root or root in path.parents for root in roots)


def cloud_synced(path):
    """The sync service that holds `path`, or None."""
    if icloud_synced(path):
        return 'iCloud'
    if sys.platform == 'win32' and onedrive_synced(path):
        return 'OneDrive'
    return None


# Where the notes stay, for the Turkish messages.
HERE = "bu Mac'te" if sys.platform == 'darwin' else 'bu bilgisayarda'


# 5-row block font for the banner; only the letters it needs.
FONT = {
    'A': [' ███ ', '█   █', '█████', '█   █', '█   █'], 'B': ['████ ', '█   █', '████ ', '█   █', '████ '],
    'C': [' ████', '█    ', '█    ', '█    ', ' ████'], 'D': ['████ ', '█   █', '█   █', '█   █', '████ '],
    'E': ['█████', '█    ', '████ ', '█    ', '█████'], 'I': ['█████', '  █  ', '  █  ', '  █  ', '█████'],
    'N': ['█   █', '██  █', '█ █ █', '█  ██', '█   █'], 'O': [' ███ ', '█   █', '█   █', '█   █', ' ███ '],
    'R': ['████ ', '█   █', '████ ', '█  █ ', '█   █'], 'S': [' ████', '█    ', ' ███ ', '    █', '████ '],
    'U': ['█   █', '█   █', '█   █', '█   █', ' ███ '], 'Y': ['█   █', ' █ █ ', '  █  ', '  █  ', '  █  '],
    'L': ['█    ', '█    ', '█    ', '█    ', '█████'], 'T': ['█████', '  █  ', '  █  ', '  █  ', '  █  '],
    'V': ['█   █', '█   █', '█   █', ' █ █ ', '  █  '],
    '.': ['     ', '     ', '     ', '     ', '  █  '], '?': [' ███ ', '█   █', '  ██ ', '     ', '  █  '],
    ' ': ['   ', '   ', '   ', '   ', '   '],
}
BANNER = 'YOUR VAULT IS READY '


def render(text):
    rows = ['' for _ in range(5)]
    for ch in text:
        glyph = FONT.get(ch.upper(), FONT[' '])
        for r in range(5):
            rows[r] += glyph[r] + ' '
    return rows


def marquee(text=BANNER, passes=1, delay=0.018, out=None):
    out = out or sys.stdout
    if not TTY:
        out.write('\n' + text.strip() + '\n')
        return
    rows = render(text)
    width = max(20, min(shutil.get_terminal_size((100, 24)).columns - 2, 160))
    tape = [' ' * width + r + ' ' * width for r in rows]
    colors = ['35', '95', '36', '96', '36']
    out.write('\033[?25l')  # hide cursor
    try:
        out.write('\n' * 6)
        for offset in range(0, (len(tape[0]) - width) * passes, 1):
            pos = offset % (len(tape[0]) - width)
            out.write('\033[6A')
            for r, row in enumerate(tape):
                out.write('\r' + paint(row[pos:pos + width], colors[r]) + '\n')
            out.write('\n')
            out.flush()
            time.sleep(delay)
    except KeyboardInterrupt:
        pass
    finally:
        out.write('\033[?25h')
        out.flush()


def run(state_dir=None, assume_yes=False, vault_path=None, language=None, animation=True, stream=None,
        semantic_default=True, app=True):
    p = Prompter(assume_yes, stream)
    total = 8
    say(paint('\n  CARRY', BOLD + ';' + MAGENTA) + paint('  · ikinci beyin kurulumu', DIM))
    say(paint(f'  Notların {HERE} kalır. Asistanın (Claude Code / Codex) onlardan alıntılı cevap verir.', DIM))
    say(paint('  Sırasıyla: workspace → vault / kaynak → ekip reposu → arama → asistan → yedek → uygulama → index.', DIM))

    # 1. Workspace
    step(1, total, 'Workspace (index ve ayarlar)')
    state = Path(p.ask('Klasör:', state_dir or '~/CarryState')).expanduser()
    if cloud_synced(state):
        warn(f'Bu klasör {cloud_synced(state)} ile senkronlanıyor; index bozulabilir. Ev klasöründe bir yer önerilir.')
        if not p.yes('Yine de devam edilsin mi?', default=False):
            state = Path('~/CarryState').expanduser()
    if (state / CONFIG_NAME).exists():
        ws = Workspace.load(state)
        ok(f'Mevcut workspace kullanılıyor: {state} ({len(ws.sources)} kaynak)')
    else:
        ws = Workspace.create(state, embedding=EmbeddingConfig(provider='hashing'),
                              retrieval=RetrievalConfig(**models.KEYWORD_ASSISTANT))
        ok(f'Workspace oluşturuldu: {state}')

    # 2. Vault or existing folder
    step(2, total, 'Bilgi kaynağı')
    kind = 'new' if vault_path or assume_yes else p.choose('Ne bağlansın?', [
        ('new', 'Yeni kişisel vault oluştur (kılavuz, şablonlar, klasörler, git)'),
        ('existing', 'Var olan bir Markdown klasörünü bağla (dokunulmaz, salt okunur)'),
        ('skip', 'Şimdilik geç')])
    project = None
    if kind == 'new':
        target = Path(vault_path or p.ask('Vault klasörü (boş olmalı):', '~/Vault')).expanduser()
        if cloud_synced(target):
            warn(f'Vault {cloud_synced(target)} ile senkronlanan bir klasörde; git ile yedeklemek daha güvenli.')
        lang = language or p.choose('Notlarının dili?', [('Turkish', 'Türkçe'), ('English', 'English')], 1)
        capture = False if assume_yes else p.yes('Prompt\'larını vault\'a kaydetsin mi (sources/carry, gözden geçirmek için)?', default=False)
        plan = vault_module.plan(target, language=lang, workspace=ws.state_dir, capture=capture, attach=True)
        say(paint(f'  {len(plan["changes"])} dosya yazılacak, mevcut dosyalara dokunulmayacak.', DIM))
        if p.yes('Vault oluşturulsun mu?'):
            result = vault_module.apply(plan, git=True)
            ok(f'Vault hazır: {result["target"]}' + (' · git başlatıldı' if result['initialised_git'] else ''))
            project = Path(result['target'])
    elif kind == 'existing':
        root = Path(p.ask('Markdown klasörü:', '~/Notes')).expanduser().resolve()
        sid = p.ask('Kaynak adı:', 'notes')
        current = Workspace.load(ws.state_dir)
        current.with_sources([*current.sources, SourceConfig(sid, root)]).save()
        ok(f'Kaynak eklendi: {sid} → {root}')
        project = root
    ws = Workspace.load(ws.state_dir)

    # 3. Team repository
    step(3, total, 'Ekip bilgi tabanı (GitHub, isteğe bağlı)')
    repo = '' if assume_yes else p.ask('Repo (sahip/ad, boş bırak = geç):', '')
    if repo:
        if not github.executable():
            install_gh = '`winget install GitHub.cli`' if sys.platform == 'win32' else '`brew install gh`'
            warn(f'GitHub CLI (gh) bulunamadı: {install_gh} ve `gh auth login`, sonra `carry github add`.')
        else:
            try:
                with Spinner(f'{repo} indiriliyor ve indeksleniyor'):
                    github.connect(ws, 'team', repo)
                ok(f'Ekip reposu bağlandı: {repo} (salt okunur, her 5 dakikada kontrol)')
            except CarryError as exc:
                warn(f'Bağlanamadı ({exc}). Sonra: carry github add --id team --repository {repo}')
        ws = Workspace.load(ws.state_dir)

    # 4. Search setting: semantic search (on by default) and the judge
    step(4, total, 'Arama ayarı')
    from . import ollama_setup
    semantic = semantic_default
    if semantic and not assume_yes:
        semantic = p.yes(f'Anlamsal arama açılsın mı? (embeddinggemma, 0,6 GB, {HERE} Ollama ile; '
                         'eş anlamlıları ve Türkçe↔İngilizce eşleşmeyi yakalar)', default=True)
    if semantic:
        st = ollama_setup.status()
        if not st['installed']:
            if sys.platform == 'win32':
                tool, offer = 'winget', 'Ollama kurulu değil. winget ile kurulsun mu (winget install Ollama.Ollama)?'
            else:
                tool, offer = 'brew', 'Ollama kurulu değil. Homebrew ile kurulsun mu (brew install ollama)?'
            if st['installer'] and (assume_yes or p.yes(offer, default=True)):
                with Spinner(f'Ollama kuruluyor ({tool})'):
                    good, how = ollama_setup.install()
                (ok if good else warn)('Ollama kuruldu ve açılışta başlayacak.' if good else f'Ollama kurulamadı ({how}).')
                semantic = good
            else:
                missing = {'brew': ' ve Homebrew yok', 'winget': ' ve winget yok'}[tool]
                warn('Ollama yok' + ('' if st['installer'] else missing) + ': https://ollama.com/download · '
                     'sonra: carry search --semantic on')
                semantic = False
        else:
            good, how = ollama_setup.start()
            if not good:
                warn(f'Ollama başlatılamadı ({how}); anahtar kelime aramasıyla devam.')
                semantic = False
    has_key = bool(jev.api_key())
    # The key store's name with its Turkish suffixes: (in it, into it).
    keychain = {'Keychain': ("Keychain'de", "Keychain'e"),
                'Windows Credential Manager': ("Kimlik Bilgisi Yöneticisi'nde", "Kimlik Bilgisi Yöneticisi'ne")
                }.get(jev.key_store())
    judge = 'jev' if has_key else 'assistant'
    if not assume_yes:
        judge = p.choose('Sonuçları kim süzsün?', [
            ('assistant', 'Asistanın küçük modeli (Haiku / gpt-reserve alt ajanı, ek hesap gerekmez)'),
            ('jev', 'TypeSafe Jev (en hızlı ve en isabetli, anahtar gerekir' + (f', {keychain[0]} var)' if has_key and keychain else ')'))],
            2 if has_key else 1)
    if judge == 'jev' and not has_key:
        say(paint('  Soru ve en fazla 32 aday parça (gizli bilgiler maskelenerek) TypeSafe\'e (ABD) gider.', DIM))
        key = p.secret('TypeSafe API anahtarı (görünmez; boş = vazgeç):') if keychain else ''
        if not keychain:
            warn('Keychain yok: anahtarı TYPESAFE_API_KEY ortam değişkeni olarak tanımlayıp setup\'ı yeniden çalıştırın.')
        if key:
            jev.store_key(key)
            ok(f'Anahtar {keychain[1]} kaydedildi.')
        else:
            judge = 'assistant'
            warn('Anahtar yok; asistanın küçük modeli süzecek.')
    mode = models.search_preset(semantic, judge)
    label = ('anlamsal arama (embeddinggemma)' if semantic else 'anahtar kelime araması') + ' · ' + \
            ('Jev süzer' if judge == 'jev' else 'asistanın küçük modeli süzer')
    try:
        with Spinner('embeddinggemma indiriliyor ve etkinleştiriliyor' if semantic else 'Arama ayarı uygulanıyor'):
            models.setup(ws, mode)
        ok(label + (' · kapatmak için: carry search --semantic off' if semantic else ''))
    except CarryError as exc:
        warn(f'Uygulanamadı ({exc}); anahtar kelime aramasıyla devam.')
        models.setup(ws, models.search_preset(False, judge))
    ws = Workspace.load(ws.state_dir)

    # 5. Clients
    step(5, total, 'Asistan bağlantısı')
    found = {c: shutil.which(c) for c in ('claude', 'codex')}
    for client, path in found.items():
        (ok if path else warn)(f'{client}: ' + ('bulundu' if path else 'bulunamadı'))
    if project and kind == 'new':
        ok('Vault içinde .mcp.json ve .codex/config.toml yazıldı; asistanı bu klasörde açınca Carry hazır.')
    elif project:
        from . import connections
        for client, path in found.items():
            if path and p.yes(f'{client} bu klasörde Carry\'ye bağlansın mı? ({project})'):
                try:
                    connections.apply(ws, connections.preview(ws, client, project, executable=path, language=language))
                    ok(f'{client} bağlandı.')
                except CarryError as exc:
                    warn(f'{client} bağlanamadı ({exc}).')

    # 6. Backup and harvest
    step(6, total, 'Yedek ve otomatik not çıkarma')
    if project and kind == 'new' and not assume_yes and github.executable():
        if p.yes('Vault için özel bir GitHub reposu açılıp yedeklensin mi?', default=False):
            name = p.ask('Repo adı:', project.name.lower())
            try:
                import subprocess
                subprocess.run(['git', '-C', str(project), 'add', '-A'], check=True, capture_output=True)
                subprocess.run(['git', '-C', str(project), 'commit', '-q', '-m', 'vault: initial'], check=True, capture_output=True)
                subprocess.run([github.executable(), 'repo', 'create', name, '--private', '--source', str(project),
                                '--remote', 'origin', '--push'], check=True, capture_output=True, text=True, encoding='utf-8')
                ok(f'Özel repo oluşturuldu ve ilk commit gönderildi: {name}')
            except (OSError, subprocess.CalledProcessError) as exc:
                warn('Yedek repo oluşturulamadı: ' + (getattr(exc, 'stderr', '') or str(exc)).strip()[:160])
    if project and kind == 'new' and not assume_yes and sys.platform in ('darwin', 'win32') and p.yes(
            'Her akşam 21:30\'da Claude Code / Codex sohbetlerinden taslak not çıkarılsın mı (inbox: +/)?', default=False):
        from . import harvest
        import subprocess
        if sys.platform == 'win32':
            try:
                harvest.install_schedule(None, ws.state_dir)  # Task Scheduler runs this Python, not a binary
                ok('Gecelik hasat kuruldu (Görev Zamanlayıcı); kapatmak için: carry harvest --remove-schedule')
            except CarryError as exc:
                warn(f'Gecelik hasat kurulamadı ({exc}); sonra: carry harvest --install-schedule')
        else:
            plist = Path.home() / 'Library' / 'LaunchAgents' / (harvest.LAUNCH_LABEL + '.plist')
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(harvest.schedule_plist(shutil.which('carry') or sys.argv[0], ws.state_dir), encoding='utf-8', newline='\n')
            subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist)], capture_output=True)
            ok('Gecelik hasat kuruldu; kapatmak için: carry harvest --remove-schedule')
    else:
        say(paint('  Sonra elle: carry harvest   (sohbetlerden taslak notlar, inbox: +/)', DIM))

    # 7. App
    step(7, total, 'Uygulama (ayarları görmek ve düzenlemek için, isteğe bağlı)')
    if app and sys.platform == 'darwin' and (assume_yes or p.yes('Carry uygulaması da kurulsun mu (~/Applications/Carry.app)?')):
        from . import app_install
        if not app_install.swiftc():
            warn('Xcode Command Line Tools gerekli: xcode-select --install · sonra: carry app install')
        else:
            try:
                with Spinner('Carry.app bu Mac\'te derleniyor'):
                    path = app_install.install(workspace=ws.state_dir)
                ok(f'Uygulama kuruldu: {path} · açmak için: carry app open')
            except CarryError as exc:
                warn(f'Uygulama kurulamadı ({exc}); sonra: carry app install')
    elif sys.platform != 'darwin':
        say(paint('  Carry uygulaması şimdilik yalnızca macOS\'ta; burada komut satırı ve asistan üzerinden çalışır.', DIM))
    else:
        say(paint('  Sonra istersen: carry app install', DIM))

    # 8. Index
    step(8, total, 'Index')
    with Spinner('Notlar indeksleniyor'):
        result = index.build(ws)
    judge = {'jev': 'TypeSafe Jev süzer', 'cross': 'yerel model süzer'}.get(ws.retrieval.reranker, 'asistanın küçük modeli süzer')
    search = 'anlamsal arama' if result.get('semantic') else 'anahtar kelime araması'
    if result.get('files'):
        ok(f'{result["files"]} dosya, {result.get("chunks", 0)} parça · {search} · {judge}')
    else:
        ok(f'Henüz not yok; ilk notun eklenince kendiliğinden indekslenir · {search} · {judge}')

    say()
    if animation:
        marquee()
    else:
        say(paint(BANNER.strip(), BOLD))
    say()
    where = project or ws.state_dir
    say(paint('  Sıradaki:', BOLD) + f' cd "{where}" && claude   ' + paint('(ya da codex)', DIM))
    say(paint('  Sor: ', DIM) + '"Vault\'umda ne var, kısaca özetle."')
    say(paint(f'  Ayarlar: {ws.state_dir} · durum: carry --workspace "{ws.state_dir}" status', DIM))
    return 0

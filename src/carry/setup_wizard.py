"""`carry setup`: a terminal wizard that creates the workspace, a vault or source,
search, client wiring and the app, builds the index and says so in large letters. It asks only
where the notes are, their language and, when Ollama is missing, whether to install it.

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

from . import github, index, models, vault as vault_module
from .config import EmbeddingConfig, RetrievalConfig, SourceConfig, Workspace, CONFIG_NAME
from .errors import CarryError
from .paths import walk_markdown

# A folder larger than this is scanned in the background when the built-in model embeds it.
BACKGROUND_FILES = 300

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


def hint(text):
    """One plain sentence before a question: what it is for, so anyone can answer it."""
    say(paint('  ' + text, DIM))


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


def _gh():
    """The GitHub CLI, or None when it is missing (github.executable raises instead)."""
    try:
        return github.executable()
    except CarryError:
        return None


# 5-row block font for the banner; only the letters it needs.
FONT = {
    'H': ['█   █', '█   █', '█████', '█   █', '█   █'], 'Z': ['█████', '   █ ', '  █  ', ' █   ', '█████'],
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
BANNER = 'CARRY HAZIR '


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


def _gh_signed_in(gh):
    import subprocess
    return subprocess.run([gh, 'auth', 'status', '--hostname', 'github.com'], capture_output=True).returncode == 0


def _team_clone(p, repository, interactive):
    """A local clone of the team repository, cloned now when missing; None when that is not possible."""
    import subprocess
    gh = _gh()
    if not gh:
        install = '`winget install GitHub.cli`' if sys.platform == 'win32' else '`brew install gh`'
        warn(f'Ekip bilgi bankasını indirmek için GitHub\'ın aracı (gh) gerekiyor ve bu bilgisayarda yok. '
             f'Kurup ({install}) kurulumu yeniden çalıştır.')
        return None
    if not _gh_signed_in(gh):
        if not interactive:
            warn('GitHub\'a giriş yapılmamış: önce `gh auth login --web`, sonra kurulumu yeniden çalıştır.')
            return None
        hint('GitHub\'a bir kez giriş yapman gerekiyor. Ekranda XXXX-XXXX gibi bir kod çıkacak; açılan sayfada')
        hint('bu kodu gir ve onayla.')
        subprocess.run([gh, 'auth', 'login', '--hostname', 'github.com', '--git-protocol', 'https', '--web'])
        if not _gh_signed_in(gh):
            warn('GitHub girişi tamamlanmadı; ekip bilgi bankası şimdilik bağlanmadı.')
            return None
    subprocess.run([gh, 'auth', 'setup-git'], capture_output=True)
    target = Path(p.ask('Nereye indirilsin?', f'~/{repository.split("/")[-1]}')).expanduser().resolve()
    if (target / '.git').is_dir():
        ok(f'Bu klasörde zaten bir kopya var, o kullanılacak: {target}')
        return target
    if target.exists() and any(target.iterdir()):
        warn(f'{target} boş değil; ekip bilgi bankası oraya indirilmedi.')
        return None
    with Spinner(f'{repository} indiriliyor'):
        done = subprocess.run([gh, 'repo', 'clone', repository, str(target)], capture_output=True, text=True, encoding='utf-8')
    if done.returncode != 0:
        warn('Ekip bilgi bankası indirilemedi: ' + (done.stderr or done.stdout).strip()[:160] +
             '. Depoya erişimin olduğundan emin ol.')
        return None
    return target


def run(state_dir=None, assume_yes=False, vault_path=None, language=None, animation=True, stream=None,
        semantic_default=True, app=True, team=None, connect=None):
    p = Prompter(assume_yes, stream)
    total = 5
    say(paint('\n  CARRY', BOLD + ';' + MAGENTA) + paint('  · kurulum', DIM))
    say(paint('  Carry, Claude ya da Codex\'in senin notlarını okuyup onlara dayanarak cevap vermesini sağlar.', DIM))
    say(paint(f'  Notların {HERE} kalır. Yalnızca birkaç soru var; köşeli parantezdeki cevap önerilendir,', DIM))
    say(paint('  emin değilsen Enter\'a basman yeterli.', DIM))

    # Carry's own folder: no question; --workspace picks another one.
    state = Path(state_dir or '~/CarryState').expanduser()
    if cloud_synced(state):
        warn(f'{state} {cloud_synced(state)} ile eşitleniyor; Carry\'nin arama dizini orada bozulabilir. Ev klasöründe bir yer daha güvenli.')
    if (state / CONFIG_NAME).exists():
        ws = Workspace.load(state)
    else:
        ws = Workspace.create(state, embedding=EmbeddingConfig(provider='hashing'),
                              retrieval=RetrievalConfig(**models.KEYWORD_ASSISTANT))

    # 1. What to connect: the owner's own notes, the team's knowledge base, or both.
    step(1, total, 'Bilgi bankası')
    if connect:
        scope = connect
    elif vault_path or assume_yes:
        scope = 'both' if team else 'own'
    else:
        scope = p.choose('Carry neye bağlansın?', [
            ('own', 'Kendi notlarıma'),
            ('team', 'Ekibin bilgi bankasına'),
            ('both', 'İkisine de')], 3 if team else 1)
    own = team_root = None
    kind = None
    if scope in ('own', 'both'):
        kind = 'new' if vault_path or assume_yes else p.choose('Kendi notların:', [
            ('new', 'Sıfırdan yeni bir not klasörü kur'),
            ('existing', 'Hazırdaki klasörümü bağla (.md dosyaları; dosyalarına dokunulmaz)')])
        if kind == 'new':
            target = Path(vault_path or p.ask('Yeni not klasörü nerede olsun?', '~/Vault')).expanduser()
            if cloud_synced(target):
                warn(f'Bu klasör {cloud_synced(target)} ile eşitleniyor. Çalışır, ama notları yedeklemenin daha güvenli yolu git.')
            lang = language or p.choose('Notlarını hangi dilde yazacaksın?', [('Turkish', 'Türkçe'), ('English', 'English')], 1)
            plan = vault_module.plan(target, language=lang, workspace=ws.state_dir, capture=False, attach=True)
            own = Path(vault_module.apply(plan, git=True)['target'])
            ok(f'Not klasörün hazır: {own}')
        else:
            own = Path(p.ask('Notlarının durduğu klasör:', '~/Notes')).expanduser().resolve()
            current = Workspace.load(ws.state_dir)
            current.with_sources([*current.sources, SourceConfig('notes', own)]).save()
            ok(f'Klasör eklendi, yalnızca okunacak: {own}')
    if scope in ('team', 'both'):
        where = 'download' if assume_yes else p.choose('Ekip bilgi bankası:', [
            ('download', 'İndir (bu bilgisayarda yoksa)'),
            ('existing', 'Bu bilgisayardaki kopyasını bağla')])
        repository = team or ''
        if where == 'download' and not repository:
            repository = p.ask('Ekip bilgi bankasının GitHub adresi (ekip liderin verir, örnek: sirket/notlar):', '')
        try:
            repository = github.repository_name(repository) if repository else ''
        except CarryError:
            warn(f'"{repository}" bir GitHub deposu adına benzemiyor.')
            repository = ''
        if where == 'existing':
            answer = p.ask('Kopyanın durduğu klasör:', f'~/{repository.split("/")[-1]}' if repository else '~/team-knowledgebase')
            team_root = Path(answer).expanduser().resolve() if answer else None
            if team_root is None or not team_root.is_dir():
                warn(f'{answer or "Klasör"} bulunamadı; ekip bilgi bankası şimdilik bağlanmadı.')
                team_root = None
        elif repository:
            team_root = _team_clone(p, repository, interactive=stream is None and not assume_yes and TTY)
        else:
            warn('Ekip deposunun adı olmadan indirilemez; ekip liderinden `carry setup --team sahip/depo` komutunu iste.')
        if team_root:
            current = Workspace.load(ws.state_dir)
            taken = {source.source_id for source in current.sources}
            source_id = next(name for name in ('team', 'team-2', 'team-3', 'team-4') if name not in taken)
            current.with_sources([*current.sources, SourceConfig(source_id, team_root, scope='team')]).save()
            ok(f'Ekip bilgi bankası bağlandı, Carry yalnızca okur: {team_root}')
    project = own or team_root
    if project is None:
        warn('Bağlanacak bir klasör kalmadı; kurulumu yeniden çalıştırabilirsin.')
        return 1
    ws = Workspace.load(ws.state_dir)

    # 2. Search: by meaning, through Ollama when it is installed, otherwise with the model
    # built into Carry. No question: the owner's assistant checks the results either way.
    step(2, total, 'Arama')
    from . import ollama_setup, onnx_model
    semantic = semantic_default
    with_ollama = False
    if semantic:
        if ollama_setup.status()['installed']:
            with_ollama, how = ollama_setup.start()
            if not with_ollama:
                warn(f'Ollama başlatılamadı ({how}); Carry\'nin kendi arama modeli kullanılacak.')
        if not with_ollama and not onnx_model.runtime_available():
            semantic = False
    label = ('Arama modeli hazırlanıyor' if with_ollama else
             f'Arama modeli indiriliyor ({onnx_model.DOWNLOAD_BYTES // 1_000_000} MB, bir iki dakika sürer)') if semantic \
        else 'Arama ayarı uygulanıyor'
    try:
        with Spinner(label):
            models.setup(ws, models.search_preset(semantic, 'assistant'))
    except CarryError as exc:
        warn(f'Anlamına göre arama açılamadı ({exc}); kelimeye göre aramayla devam ediliyor.')
        semantic = False
        models.setup(ws, models.search_preset(False, 'assistant'))
    ok('Anlamına göre arama açık.' if semantic else
       'Kelimeye göre arama açık. Bu bilgisayarda anlamına göre arama için Ollama gerekiyor: '
       'carry search --semantic on --install-ollama')
    ws = Workspace.load(ws.state_dir)

    # 3. Assistants: a new notes folder carries its own wiring; an existing one is connected for each assistant found.
    step(3, total, 'Asistan bağlantısı')
    found = {c: shutil.which(c) for c in ('claude', 'codex')}
    names = {'claude': 'Claude Code', 'codex': 'Codex'}
    if kind == 'new':
        ok('Claude Code ya da Codex\'i not klasöründe açtığında Carry\'yi kullanır.')
    # An existing folder and the team copy get the Carry tool for every assistant found.
    from . import connections
    for folder in [f for f in (own if kind == 'existing' else None, team_root) if f]:
        for client, path in found.items():
            if not path:
                continue
            try:
                connections.apply(ws, connections.preview(ws, client, folder, executable=path, language=language))
                ok(f'{names[client]} bağlandı: {folder}')
            except CarryError as exc:
                if str(exc) == 'existing_carry_connection_conflict':
                    warn(f'{names[client]}: {folder} daha önce başka bir Carry kurulumuna bağlanmış, ona dokunulmadı.')
                else:
                    warn(f'{names[client]} bağlanamadı ({exc}).')
    if not any(found.values()):
        warn('Bu bilgisayarda Claude Code da Codex de bulunamadı; birini kurduktan sonra not klasöründe aç.')

    # 4. App (macOS)
    step(4, total, 'Carry uygulaması')
    if not app:
        say(paint('  Sonra istersen: carry app install', DIM))
    elif sys.platform != 'darwin':
        say(paint('  Carry uygulaması şimdilik yalnızca macOS\'ta; burada komut satırı ve asistan üzerinden çalışır.', DIM))
    else:
        from . import app_install
        if not app_install.swiftc():
            warn('Uygulama için Apple\'ın ücretsiz geliştirici araçları gerekiyor: xcode-select --install '
                 '(açılan pencerede "Yükle"), sonra: carry app install')
        else:
            try:
                with Spinner('Carry uygulaması bu Mac\'te hazırlanıyor (bir dakika kadar)'):
                    path = app_install.install(workspace=ws.state_dir)
                ok(f'Uygulama kuruldu: {path}')
            except CarryError as exc:
                warn(f'Uygulama kurulamadı ({exc}); sonra: carry app install')

    # 5. Index
    step(5, total, 'Notlar aranabilir hale getiriliyor')
    count = sum(1 for source in ws.sources if Path(source.root).is_dir() for _ in walk_markdown(Path(source.root)))
    if ws.embedding.provider == 'onnx' and count > BACKGROUND_FILES:
        # The built-in model reads about six passages a second: a large folder takes a while.
        from . import maintenance
        maintenance.start(ws, 'index')
        ok(f'{count} not arka planda taranıyor; büyük bir klasörde bu yarım saati bulabilir. '
           f'Bu arada kurulum bitti, Carry tarama bitince aramaya başlar.')
    else:
        with Spinner('Notlar taranıyor'):
            result = index.build(ws)
        ok(f'{result["files"]} not tarandı.' if result.get('files') else 'Henüz not yok; ilk notunu eklediğinde kendiliğinden taranır.')

    say()
    if animation:
        marquee()
    else:
        say(paint(BANNER.strip(), BOLD))
    say()
    say(paint('  Sıradaki adım:', BOLD) + ' Claude Code\'u (ya da Codex\'i) not klasöründe aç:')
    say(f'    cd "{project}" && claude')
    say(paint('  İlk açılışta "carry" aracını kullanmak için izin ister; onayla.', DIM))
    say(paint('  Sonra sor: ', DIM) + '"Notlarımda ne var, kısaca özetle."')
    say()
    if own and team_root:
        say(paint(f'  Ekip bilgi bankası her iki klasörde de aranır; ona yazmak için Claude\'u {team_root} içinde aç.', DIM))
    say()
    say(paint('  İstersen sonra:', DIM))
    if own:
        say(paint('    her akşam sohbetlerden taslak not   carry harvest --install-schedule', DIM))
    say(paint('    Carry\'yi başka bir klasörde de kullan   carry connect claude <klasör>', DIM))
    return 0

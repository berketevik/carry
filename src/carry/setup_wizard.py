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


def run(state_dir=None, assume_yes=False, vault_path=None, language=None, animation=True, stream=None,
        semantic_default=True, app=True):
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

    # 1. Notes
    step(1, total, 'Notların')
    kind = 'new' if vault_path or assume_yes else p.choose('Notların nerede dursun?', [
        ('new', 'Yeni bir not klasörü kur (önerilen)'),
        ('existing', 'Notlarım (.md dosyaları) zaten bir klasörde, onu kullan (dosyalarına dokunulmaz)')])
    if kind == 'new':
        target = Path(vault_path or p.ask('Yeni not klasörü nerede olsun?', '~/Vault')).expanduser()
        if cloud_synced(target):
            warn(f'Bu klasör {cloud_synced(target)} ile eşitleniyor. Çalışır, ama notları yedeklemenin daha güvenli yolu git.')
        lang = language or p.choose('Notlarını hangi dilde yazacaksın?', [('Turkish', 'Türkçe'), ('English', 'English')], 1)
        plan = vault_module.plan(target, language=lang, workspace=ws.state_dir, capture=False, attach=True)
        result = vault_module.apply(plan, git=True)
        project = Path(result['target'])
        ok(f'Not klasörün hazır: {project}')
    else:
        project = Path(p.ask('Notlarının durduğu klasör:', '~/Notes')).expanduser().resolve()
        current = Workspace.load(ws.state_dir)
        current.with_sources([*current.sources, SourceConfig('notes', project)]).save()
        ok(f'Klasör eklendi, yalnızca okunacak: {project}')
    ws = Workspace.load(ws.state_dir)

    # 2. Search: by meaning when Ollama is (or can be) there; the owner's assistant checks the results.
    step(2, total, 'Arama')
    from . import ollama_setup
    semantic = semantic_default
    if semantic:
        st = ollama_setup.status()
        if st['installed']:
            good, how = ollama_setup.start()
            if not good:
                warn(f'Ollama başlatılamadı ({how}); kelimeye göre aramayla devam ediliyor.')
                semantic = False
        elif st['installer']:
            tool = 'winget' if sys.platform == 'win32' else 'Homebrew'
            if not assume_yes:
                hint('Carry notlarını anlamına göre de arar: farklı kelimelerle yazılmış notları da bulur. Bunun için')
                hint(f'Ollama adlı ücretsiz bir program ve 0,6 GB\'lık bir model {HERE} kurulur; notların internete gitmez.')
            if p.yes(f'Ollama kurulsun mu? ({tool} ile, birkaç dakika sürer)', default=True):
                with Spinner('Ollama kuruluyor'):
                    semantic, how = ollama_setup.install()
                (ok if semantic else warn)('Ollama kuruldu.' if semantic else f'Ollama kurulamadı ({how}); kelimeye göre aramayla devam ediliyor.')
            else:
                semantic = False
        else:
            semantic = False
    try:
        with Spinner('Arama modeli indiriliyor (0,6 GB, birkaç dakika sürebilir)' if semantic else 'Arama ayarı uygulanıyor'):
            models.setup(ws, models.search_preset(semantic, 'assistant'))
    except CarryError as exc:
        warn(f'Anlamına göre arama açılamadı ({exc}); kelimeye göre aramayla devam ediliyor.')
        semantic = False
        models.setup(ws, models.search_preset(False, 'assistant'))
    ok('Anlamına göre arama açık.' if semantic else
       'Kelimeye göre arama açık. Anlamına göre arama sonra eklenebilir: carry search --semantic on --install-ollama')
    ws = Workspace.load(ws.state_dir)

    # 3. Assistants: a new notes folder carries its own wiring; an existing one is connected for each assistant found.
    step(3, total, 'Asistan bağlantısı')
    found = {c: shutil.which(c) for c in ('claude', 'codex')}
    names = {'claude': 'Claude Code', 'codex': 'Codex'}
    if kind == 'new':
        ok('Claude Code ya da Codex\'i not klasöründe açtığında Carry\'yi kullanır.')
    else:
        from . import connections
        for client, path in found.items():
            if not path:
                continue
            try:
                connections.apply(ws, connections.preview(ws, client, project, executable=path, language=language))
                ok(f'{names[client]} bağlandı.')
            except CarryError as exc:
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
    say(paint('  İstersen sonra:', DIM))
    say(paint('    ekibin GitHub\'daki ortak notları   carry github add --id team --repository sahip/depo', DIM))
    say(paint('    her akşam sohbetlerden taslak not   carry harvest --install-schedule', DIM))
    return 0

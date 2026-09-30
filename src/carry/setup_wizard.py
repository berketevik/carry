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
    total = 8
    say(paint('\n  CARRY', BOLD + ';' + MAGENTA) + paint('  · kurulum', DIM))
    say(paint('  Carry, Claude ya da Codex\'in senin notlarını okuyup onlara dayanarak cevap vermesini sağlar.', DIM))
    say(paint(f'  Notların {HERE} kalır. 8 kısa adım var; köşeli parantezdeki cevap önerilendir,', DIM))
    say(paint('  emin değilsen Enter\'a basman yeterli.', DIM))

    # 1. Workspace
    step(1, total, 'Carry\'nin kendi klasörü')
    if not assume_yes:
        hint('Carry arama dizinini ve ayarlarını burada tutar; notların burada durmaz. Önerilen yer uygundur.')
    state = Path(p.ask('Klasör:', state_dir or '~/CarryState')).expanduser()
    if cloud_synced(state):
        warn(f'Bu klasör {cloud_synced(state)} ile eşitleniyor. Eşitleme Carry\'nin arama dizinini bozabilir; ev klasöründe bir yer daha güvenli.')
        if not p.yes('Yine de bu klasör kullanılsın mı?', default=False):
            state = Path('~/CarryState').expanduser()
    if (state / CONFIG_NAME).exists():
        ws = Workspace.load(state)
        ok(f'Daha önce kurulmuş Carry klasörü kullanılıyor: {state}')
    else:
        ws = Workspace.create(state, embedding=EmbeddingConfig(provider='hashing'),
                              retrieval=RetrievalConfig(**models.KEYWORD_ASSISTANT))
        ok(f'Carry klasörü hazır: {state}')

    # 2. Vault or existing folder
    step(2, total, 'Notların nerede duracak?')
    kind = 'new' if vault_path or assume_yes else p.choose('Ne yapalım?', [
        ('new', 'Yeni bir not klasörü kur (önerilen; hazır klasör düzeni ve rehberle gelir)'),
        ('existing', 'Notlarım (.md dosyaları) zaten bir klasörde, onu kullan (dosyalarına dokunulmaz)'),
        ('skip', 'Şimdilik geç')])
    project = None
    if kind == 'new':
        target = Path(vault_path or p.ask('Yeni not klasörü nerede olsun? (boş ya da henüz olmayan bir klasör):', '~/Vault')).expanduser()
        if cloud_synced(target):
            warn(f'Bu klasör {cloud_synced(target)} ile eşitleniyor. Çalışır, ama notları yedeklemenin daha güvenli yolu GitHub (6. adım).')
        lang = language or p.choose('Notlarını hangi dilde yazacaksın?', [('Turkish', 'Türkçe'), ('English', 'English')], 1)
        capture = False if assume_yes else p.yes('Asistana yazdığın her mesajın bir kopyası da not klasörüne kaydedilsin mi? (sonradan göz atmak için; çoğu kişi istemez)', default=False)
        plan = vault_module.plan(target, language=lang, workspace=ws.state_dir, capture=capture, attach=True)
        say(paint(f'  {len(plan["changes"])} dosya yazılacak, var olan dosyalara dokunulmayacak.', DIM))
        if p.yes('Not klasörü oluşturulsun mu?'):
            result = vault_module.apply(plan, git=True)
            ok(f'Not klasörün hazır: {result["target"]}' + (' · değişiklik geçmişi (git) açıldı' if result['initialised_git'] else ''))
            project = Path(result['target'])
    elif kind == 'existing':
        root = Path(p.ask('Notlarının durduğu klasör:', '~/Notes')).expanduser().resolve()
        sid = p.ask('Bu klasöre kısa bir ad ver (Carry içinde böyle görünür):', 'notes')
        current = Workspace.load(ws.state_dir)
        current.with_sources([*current.sources, SourceConfig(sid, root)]).save()
        ok(f'Klasör eklendi: {sid} → {root}')
        project = root
    ws = Workspace.load(ws.state_dir)

    # 3. Team repository
    step(3, total, 'Ekibinin ortak notları (isteğe bağlı)')
    if not assume_yes:
        hint('Ekibinin notları GitHub\'da bir depoda duruyorsa Carry onları da arar, ama değiştirmez.')
        hint('Böyle bir depo yoksa ya da bilmiyorsan boş bırakıp Enter\'a bas.')
    repo = '' if assume_yes else p.ask('GitHub deposu (örnek: sirket/notlar):', '')
    if repo:
        if not _gh():
            install_gh = '`winget install GitHub.cli`' if sys.platform == 'win32' else '`brew install gh`'
            warn(f'Bunun için GitHub\'ın komut satırı aracı (gh) gerekiyor ve bu bilgisayarda yok. Kurmak için {install_gh}, '
                 f'sonra `gh auth login` ile giriş yap ve `carry github add` çalıştır. Kurulum ekip notları olmadan sürüyor.')
        else:
            try:
                with Spinner(f'{repo} indiriliyor ve aranabilir hale getiriliyor'):
                    github.connect(ws, 'team', repo)
                ok(f'Ekip notları bağlandı: {repo} (yalnızca okunur, 5 dakikada bir güncellenir)')
            except CarryError as exc:
                warn(f'Ekip notlarına bağlanılamadı ({exc}). Depoya erişimin olduğundan ve `gh auth login` ile giriş yaptığından emin ol, '
                     f'sonra: carry github add --id team --repository {repo}')
        ws = Workspace.load(ws.state_dir)

    # 4. Search setting: semantic search (on by default) and the judge
    step(4, total, 'Arama')
    from . import ollama_setup
    semantic = semantic_default
    if semantic and not assume_yes:
        hint('Carry notlarını kelimelere göre arar. İstersen anlamına göre de arar: farklı kelimelerle yazılmış')
        hint('notları ve Türkçe-İngilizce eşleşmeleri de bulur. Bunun için Ollama adlı ücretsiz bir program ve')
        hint(f'0,6 GB\'lık bir model {HERE} kurulur; notların internete gitmez.')
        semantic = p.yes('Anlamına göre arama açılsın mı?', default=True)
    if semantic:
        st = ollama_setup.status()
        if not st['installed']:
            if sys.platform == 'win32':
                tool, offer = 'winget', 'Ollama bu bilgisayarda yok. Şimdi kurulsun mu? (winget ile, birkaç dakika sürer)'
            else:
                tool, offer = 'brew', 'Ollama bu bilgisayarda yok. Şimdi kurulsun mu? (Homebrew ile, birkaç dakika sürer)'
            if st['installer'] and (assume_yes or p.yes(offer, default=True)):
                with Spinner(f'Ollama kuruluyor ({tool})'):
                    good, how = ollama_setup.install()
                (ok if good else warn)('Ollama kuruldu; bilgisayar her açıldığında kendiliğinden başlar.' if good
                                        else f'Ollama kurulamadı ({how}); kelimeye göre aramayla devam ediliyor.')
                semantic = good
            else:
                missing = {'brew': ' ve bu bilgisayarda Homebrew yok', 'winget': ' ve bu bilgisayarda winget yok'}[tool]
                warn('Ollama kurulmadı' + ('' if st['installer'] else missing) + '. Elle kurmak için https://ollama.com/download, '
                     'sonra: carry search --semantic on. Şimdilik kelimeye göre arama kullanılacak.')
                semantic = False
        else:
            good, how = ollama_setup.start()
            if not good:
                warn(f'Ollama başlatılamadı ({how}); kelimeye göre aramayla devam ediliyor.')
                semantic = False
    has_key = bool(jev.api_key())
    # The key store's name with its Turkish suffixes: (in it, into it).
    keychain = {'Keychain': ("Keychain'de", "Keychain'e"),
                'Windows Credential Manager': ("Kimlik Bilgisi Yöneticisi'nde", "Kimlik Bilgisi Yöneticisi'ne")
                }.get(jev.key_store())
    judge = 'jev' if has_key else 'assistant'
    if not assume_yes:
        hint('Aramadan birkaç not parçası çıkar; soruna gerçekten cevap verenleri seçmek için bir kontrol daha yapılır.')
        judge = p.choose('Bu kontrolü kim yapsın?', [
            ('assistant', 'Kendi asistanım (Claude ya da Codex; ek hesap, anahtar ya da ücret gerekmez)'),
            ('jev', 'TypeSafe Jev (ayrı bir çevrimiçi hizmet; daha hızlı ve biraz daha isabetli, ücretli bir erişim anahtarı ister'
                    + (f'; anahtarın {keychain[0]} kayıtlı)' if has_key and keychain else ')'))],
            2 if has_key else 1)
    if judge == 'jev' and not has_key:
        say(paint('  Jev ile soru ve bulunan en fazla 32 not parçası (şifre gibi gizli bilgiler gizlenerek) TypeSafe\'in ABD\'deki sunucularına gider.', DIM))
        key = p.secret('TypeSafe erişim anahtarını yapıştır (yazarken görünmez; vazgeçmek için boş bırak):') if keychain else ''
        if not keychain:
            warn('Bu bilgisayarda anahtarı güvenle saklayacak bir yer yok: anahtarı TYPESAFE_API_KEY ortam değişkeni olarak tanımlayıp kurulumu yeniden çalıştır.')
        if key:
            jev.store_key(key)
            ok(f'Anahtar {keychain[1]} kaydedildi.')
        else:
            judge = 'assistant'
            warn('Anahtar girilmedi; kontrolü kendi asistanın yapacak.')
    mode = models.search_preset(semantic, judge)
    label = ('anlamına göre arama' if semantic else 'kelimeye göre arama') + ' · ' + \
            ('kontrol: TypeSafe Jev' if judge == 'jev' else 'kontrol: kendi asistanın')
    try:
        with Spinner('Arama modeli indiriliyor (0,6 GB, birkaç dakika sürebilir)' if semantic else 'Arama ayarı uygulanıyor'):
            models.setup(ws, mode)
        ok(label + (' · kapatmak için: carry search --semantic off' if semantic else ''))
    except CarryError as exc:
        warn(f'Anlamına göre arama açılamadı ({exc}); kelimeye göre aramayla devam ediliyor.')
        models.setup(ws, models.search_preset(False, judge))
    ws = Workspace.load(ws.state_dir)

    # 5. Clients
    step(5, total, 'Asistan bağlantısı')
    found = {c: shutil.which(c) for c in ('claude', 'codex')}
    names = {'claude': 'Claude Code', 'codex': 'Codex'}
    for client, path in found.items():
        (ok if path else warn)(f'{names[client]}: ' + ('bu bilgisayarda var' if path else 'bu bilgisayarda yok'))
    if project and kind == 'new':
        ok('Not klasörüne asistan ayarları yazıldı: Claude ya da Codex\'i bu klasörde açtığında Carry\'yi kullanabilir.')
    elif project:
        from . import connections
        for client, path in found.items():
            if path and p.yes(f'{names[client]}, bu klasörde açıldığında Carry\'yi kullanabilsin mi? ({project})'):
                try:
                    connections.apply(ws, connections.preview(ws, client, project, executable=path, language=language))
                    ok(f'{names[client]} bağlandı.')
                except CarryError as exc:
                    warn(f'{names[client]} bağlanamadı ({exc}).')

    # 6. Backup and harvest
    step(6, total, 'Yedek ve otomatik notlar')
    if project and kind == 'new' and not assume_yes and _gh():
        hint('Notlarının bir kopyası GitHub hesabında, yalnızca senin görebileceğin gizli bir depoda tutulabilir.')
        if p.yes('Notların GitHub\'da gizli bir depoya yedeklensin mi?', default=False):
            name = p.ask('Deponun adı:', project.name.lower())
            try:
                import subprocess
                subprocess.run(['git', '-C', str(project), 'add', '-A'], check=True, capture_output=True)
                subprocess.run(['git', '-C', str(project), 'commit', '-q', '-m', 'vault: initial'], check=True, capture_output=True)
                subprocess.run([_gh(), 'repo', 'create', name, '--private', '--source', str(project),
                                '--remote', 'origin', '--push'], check=True, capture_output=True, text=True, encoding='utf-8')
                ok(f'Yedek deposu oluşturuldu ve notların ilk kez yedeklendi: {name}')
            except (OSError, subprocess.CalledProcessError) as exc:
                warn('Yedek deposu oluşturulamadı: ' + (getattr(exc, 'stderr', '') or str(exc)).strip()[:160])
    nightly = project and kind == 'new' and not assume_yes and sys.platform in ('darwin', 'win32')
    if nightly:
        hint('Carry her akşam o günün sohbetlerini okuyup verilen kararları ve yarım kalan işleri taslak not olarak')
        hint('not klasöründeki "+" klasörüne koyabilir. Taslaklar sen onaylamadan nota dönüşmez.')
    if nightly and p.yes('Bu her akşam 21:30\'da yapılsın mı?', default=False):
        from . import harvest
        import subprocess
        if sys.platform == 'win32':
            try:
                harvest.install_schedule(None, ws.state_dir)  # Task Scheduler runs this Python, not a binary
                ok('Akşam taslakları açıldı (Görev Zamanlayıcı); kapatmak için: carry harvest --remove-schedule')
            except CarryError as exc:
                warn(f'Akşam taslakları kurulamadı ({exc}); sonra: carry harvest --install-schedule')
        else:
            plist = Path.home() / 'Library' / 'LaunchAgents' / (harvest.LAUNCH_LABEL + '.plist')
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(harvest.schedule_plist(shutil.which('carry') or sys.argv[0], ws.state_dir), encoding='utf-8', newline='\n')
            subprocess.run(['launchctl', 'bootstrap', f'gui/{os.getuid()}', str(plist)], capture_output=True)
            ok('Akşam taslakları açıldı; kapatmak için: carry harvest --remove-schedule')
    else:
        say(paint('  İstediğin zaman elle: carry harvest   (sohbetlerden taslak notlar, "+" klasörüne)', DIM))

    # 7. App
    step(7, total, 'Carry uygulaması (isteğe bağlı)')
    if app and sys.platform == 'darwin' and not assume_yes:
        hint('Uygulamada akşam taslaklarını onaylar, ayarları değiştirir ve notlarına göz atarsın.')
    if app and sys.platform == 'darwin' and (assume_yes or p.yes('Carry uygulaması da kurulsun mu?')):
        from . import app_install
        if not app_install.swiftc():
            warn('Uygulama için Apple\'ın ücretsiz geliştirici araçları gerekiyor: xcode-select --install '
                 '(açılan pencerede "Yükle"), sonra: carry app install')
        else:
            try:
                with Spinner('Carry uygulaması bu Mac\'te hazırlanıyor (bir dakika kadar)'):
                    path = app_install.install(workspace=ws.state_dir)
                ok(f'Uygulama kuruldu: {path} · açmak için: carry app open')
            except CarryError as exc:
                warn(f'Uygulama kurulamadı ({exc}); sonra: carry app install')
    elif sys.platform != 'darwin':
        say(paint('  Carry uygulaması şimdilik yalnızca macOS\'ta; burada komut satırı ve asistan üzerinden çalışır.', DIM))
    else:
        say(paint('  Sonra istersen: carry app install', DIM))

    # 8. Index
    step(8, total, 'Notlar aranabilir hale getiriliyor')
    with Spinner('Notlar taranıyor'):
        result = index.build(ws)
    judge = {'jev': 'kontrol: TypeSafe Jev', 'cross': 'kontrol: bu bilgisayardaki model'}.get(ws.retrieval.reranker, 'kontrol: kendi asistanın')
    search = 'anlamına göre arama' if result.get('semantic') else 'kelimeye göre arama'
    if result.get('files'):
        ok(f'{result["files"]} not tarandı · {search} · {judge}')
    else:
        ok(f'Henüz not yok; ilk notunu eklediğinde kendiliğinden taranır · {search} · {judge}')

    say()
    if animation:
        marquee()
    else:
        say(paint(BANNER.strip(), BOLD))
    say()
    where = project or ws.state_dir
    say(paint('  Sıradaki adım:', BOLD) + ' Claude Code\'u (ya da Codex\'i) not klasöründe aç:')
    say(f'    cd "{where}" && claude')
    say(paint('  İlk açılışta bu klasördeki "carry" aracını kullanmak için izin ister; onayla.', DIM))
    say(paint('  Sonra sor: ', DIM) + '"Notlarımda ne var, kısaca özetle."')
    say(paint(f'  Ayarlar: {ws.state_dir} · durum: carry --workspace "{ws.state_dir}" status', DIM))
    return 0

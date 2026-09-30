"""Find, install and start Ollama for semantic search (embeddinggemma).

Installation goes through Homebrew (`brew install ollama`, then `brew services start
ollama` so the server comes back after a restart); on Windows through winget, whose
Ollama starts its tray app at every sign-in. An existing Ollama.app or binary is
reused. Without an installer the caller is told where to get Ollama and search
stays keyword-only; nothing is downloaded behind the user's back.
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.request

from .background import detached, which
from .models import ollama_binary, windows_ollama_dir

ENDPOINT = 'http://127.0.0.1:11434'
APP = '/Applications/Ollama.app'


def running(endpoint=ENDPOINT, timeout=2):
    try:
        with urllib.request.urlopen(endpoint + '/api/version', timeout=timeout) as response:
            return bool(json.load(response).get('version'))
    except Exception:
        return False


def installer():
    """The package manager Carry installs Ollama with here: brew, or winget on Windows."""
    return which('winget') if sys.platform == 'win32' else shutil.which('brew')


def status():
    binary = ollama_binary()
    return dict(installed=bool(binary), binary=binary, running=running(),
                brew=shutil.which('brew'), installer=installer(), app=shutil.os.path.isdir(APP))


def _wait(seconds=30):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if running():
            return True
        time.sleep(0.5)
    return False


def install():
    """Homebrew install plus a login service (winget on Windows). Returns (ok, message)."""
    if sys.platform == 'win32':
        winget = installer()
        if not winget:
            return False, 'winget_missing'
        step = subprocess.run([winget, 'install', '--id', 'Ollama.Ollama', '--exact', '--silent',
                               '--accept-package-agreements', '--accept-source-agreements'],
                              capture_output=True, text=True, encoding='utf-8', errors='replace')
        if step.returncode != 0 and not ollama_binary():
            return False, (step.stderr or step.stdout).strip()[-300:] or 'winget_install_failed'
        return start()
    brew = shutil.which('brew')
    if not brew:
        return False, 'homebrew_missing'
    step = subprocess.run([brew, 'install', 'ollama'], capture_output=True, text=True)
    if step.returncode != 0:
        return False, (step.stderr or step.stdout).strip()[-300:] or 'brew_install_failed'
    return start()


def start():
    """Start a persistent server: brew service, else the app, else a detached `ollama serve`."""
    if running():
        return True, 'running'
    brew = shutil.which('brew')
    if brew:
        listed = subprocess.run([brew, 'list', '--formula', 'ollama'], capture_output=True, text=True)
        if listed.returncode == 0:
            subprocess.run([brew, 'services', 'start', 'ollama'], capture_output=True, text=True)
            if _wait():
                return True, 'brew_service'
    if shutil.os.path.isdir(APP):
        subprocess.run(['open', '-a', 'Ollama'], capture_output=True)
        if _wait():
            return True, 'app'
    tray = windows_ollama_dir() / 'ollama app.exe'
    if sys.platform == 'win32' and tray.is_file():
        # The tray app runs the server and starts again at every sign-in, like the brew service.
        detached([str(tray)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if _wait():
            return True, 'app'
    binary = ollama_binary()
    if binary:
        detached([binary, 'serve'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                 stderr=subprocess.DEVNULL)
        if _wait():
            return True, 'serve'
    return False, 'ollama_not_started'


def ensure(install_if_missing=False):
    """(ok, how) with Ollama installed and running."""
    if not status()['installed']:
        if not install_if_missing:
            return False, 'ollama_missing'
        return install()
    return start()

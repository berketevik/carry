"""`carry app install`: build the macOS app on this Mac against the installed Carry.

The app is a SwiftUI shell over `carry.desktop`. Instead of bundling a Python
runtime it records the interpreter of this installation, so it always matches the
installed version and `uv tool upgrade carry` updates both. Built locally, it is
not quarantined; it is ad-hoc signed. Needs the Xcode Command Line Tools (swiftc).
"""
from importlib import resources
import os
from pathlib import Path
import platform
import plistlib
import shutil
import subprocess
import sys
import tempfile

from .errors import CarryError

BUNDLE_ID = 'local.carry.alpha'
DEFAULT_PATH = Path.home() / 'Applications' / 'Carry.app'


def swiftc():
    found = shutil.which('swiftc')
    if found:
        return found
    try:
        out = subprocess.run(['xcrun', '--find', 'swiftc'], capture_output=True, text=True, timeout=20)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _ours(app):
    try:
        with open(app / 'Contents' / 'Info.plist', 'rb') as f:
            return plistlib.load(f).get('CFBundleIdentifier') == BUNDLE_ID
    except (OSError, ValueError):
        return False


def install(target=DEFAULT_PATH, workspace=None, python=None):
    if sys.platform != 'darwin':
        raise CarryError('app_requires_macos')
    compiler = swiftc()
    if not compiler:
        raise CarryError('swiftc_missing')
    target = Path(target).expanduser()
    if target.exists() and not _ours(target):
        raise CarryError('app_path_taken')
    source = resources.files('carry') / 'app' / 'CarryApp.swift'
    arch = 'arm64' if platform.machine() == 'arm64' else 'x86_64'
    with tempfile.TemporaryDirectory(prefix='carry-app-') as tmp:
        app = Path(tmp) / 'Carry.app'
        (app / 'Contents' / 'MacOS').mkdir(parents=True)
        (app / 'Contents' / 'Resources').mkdir()
        swift = Path(tmp) / 'CarryApp.swift'
        swift.write_text(source.read_text(encoding='utf-8'), encoding='utf-8')
        build = subprocess.run([compiler, '-O', '-parse-as-library', '-swift-version', '5',
                                '-target', f'{arch}-apple-macosx14.0', '-module-cache-path', str(Path(tmp) / 'cache'),
                                str(swift), '-o', str(app / 'Contents' / 'MacOS' / 'Carry')],
                               capture_output=True, text=True)
        if build.returncode != 0:
            raise CarryError('app_build_failed: ' + (build.stderr or build.stdout).strip()[-300:])
        with open(app / 'Contents' / 'Info.plist', 'wb') as f:
            plistlib.dump(dict(CFBundleExecutable='Carry', CFBundleIdentifier=BUNDLE_ID, CFBundleName='Carry',
                               CFBundleDisplayName='Carry', CFBundlePackageType='APPL', CFBundleShortVersionString=_version(),
                               CFBundleVersion='1', LSMinimumSystemVersion='14.0', NSHighResolutionCapable=True), f)
        (app / 'Contents' / 'Resources' / 'python-path.txt').write_text((python or sys.executable) + '\n')
        subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(app)], capture_output=True)
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(app, target, symlinks=True)
    if workspace:
        # The app opens the workspace it last used; point it at this one.
        subprocess.run(['defaults', 'write', BUNDLE_ID, 'workspace', str(workspace)], capture_output=True)
    return target


def remove(target=DEFAULT_PATH):
    target = Path(target).expanduser()
    if target.exists():
        if not _ours(target):
            raise CarryError('app_path_taken')
        shutil.rmtree(target)
        return True
    return False


def _version():
    try:
        from importlib.metadata import version
        return version('carry')
    except Exception:
        return '0'

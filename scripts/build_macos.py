"""Build a local arm64 .app using an explicit relocatable CPython distribution.

Example: python3 scripts/build_macos.py --python-root /path/to/standalone-python
The output must not exist. No downloads, global installs, signing identities or
publication. Keep runtime notices; this spike is not a distribution licence audit.
"""
import argparse
import ctypes
import os
import sys
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import tempfile


def clone_copy(source, destination):
    # APFS copy-on-write avoids duplicating a large runtime during packaging.
    if sys.platform == 'darwin':
        clone = ctypes.CDLL(None, use_errno=True).clonefile
        clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        if clone(os.fsencode(source), os.fsencode(destination), 0) == 0:
            shutil.copystat(source, destination)
            return destination
    return shutil.copy2(source, destination)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('build/Carry.app'))
    parser.add_argument('--swift-overlay', type=Path, help='Optional compiler VFS overlay for a locally diagnosed toolchain issue')
    parser.add_argument('--gh-binary', type=Path, help='Official GitHub CLI binary to bundle')
    parser.add_argument('--gh-license', type=Path, help='GitHub CLI license file')
    parser.add_argument('--ollama-resources', type=Path, help='Ollama runtime resources to bundle')
    parser.add_argument('--ollama-license', type=Path, help='Ollama license file')
    parser.add_argument('--view-tests', action='store_true', help='Build a separate native view/bridge test executable')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    runtime = args.python_root.expanduser().resolve()
    destination = args.output.resolve()
    if destination.exists():
        parser.error('output already exists; choose a new output directory')
    if not (runtime / 'bin/python3').is_file():
        parser.error('standalone Python bin/python3 is required')
    # Reject a venv or an external symlink masquerading as a bundled runtime.
    if (runtime / 'pyvenv.cfg').exists():
        parser.error('a venv is not a relocatable runtime')
    for p in runtime.rglob('*'):
        if p.is_symlink() and not p.resolve().is_relative_to(runtime):
            parser.error('runtime contains an external symlink: ' + str(p.relative_to(runtime)))
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='carry-build-') as temp:
        app = Path(temp).resolve() / 'Carry.app'
        resources = app / 'Contents/Resources'
        binary = app / 'Contents/MacOS/Carry'
        binary.parent.mkdir(parents=True)
        resources.mkdir(parents=True)
        shutil.copytree(runtime, resources / 'python', symlinks=True, copy_function=clone_copy,
                        ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        if args.gh_binary:
            if not args.gh_license or not args.gh_license.is_file():
                parser.error('--gh-license is required when bundling GitHub CLI')
            (resources / 'bin').mkdir()
            shutil.copy2(args.gh_binary, resources / 'bin/gh')
            shutil.copy2(args.gh_license, resources / 'bin/GITHUB-CLI-LICENSE')
        if args.ollama_resources:
            if not args.ollama_license or not args.ollama_license.is_file():
                parser.error('--ollama-license is required when bundling Ollama')
            shutil.copytree(args.ollama_resources, resources / 'ollama', symlinks=True, copy_function=clone_copy,
                ignore=shutil.ignore_patterns('*.png', '*.svg', '*.icns'))
            shutil.copy2(args.ollama_license, resources / 'ollama/OLLAMA-LICENSE')
        python = resources / 'python/bin/python3'
        env = {'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'}
        info = json.loads(subprocess.check_output([str(python), '-I', '-c',
            'import sys,sysconfig,json; print(json.dumps(dict(version=sys.version.split()[0],site=sysconfig.get_path("purelib"))))'], env=env, text=True))
        site = Path(info['site'])
        if not site.is_relative_to(resources):
            raise RuntimeError('Python did not relocate its site-packages')
        target = site / 'carry'
        if target.exists():
            raise RuntimeError('runtime must not contain an older Carry install')
        shutil.copytree(root / 'src/carry', target, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        subprocess.run(['/usr/bin/swiftc', '-O', '-parse-as-library', '-swift-version', '5',
            *(['-D', 'CARRY_VIEW_TESTS', str(root / 'macos/CarryViewTests.swift')] if args.view_tests else []),
            *(['-vfsoverlay', str(args.swift_overlay.resolve())] if args.swift_overlay else []),
            '-target', 'arm64-apple-macosx14.0', '-module-cache-path', str(args.swift_overlay.parent / 'swift-fixed-cache') if args.swift_overlay else str(root / 'build/swift-cache'),
            str(root / 'macos/CarryApp.swift'), '-o', str(binary)], check=True)
        with (app / 'Contents/Info.plist').open('wb') as out:
            plistlib.dump(dict(CFBundleExecutable='Carry', CFBundleIdentifier='local.carry.alpha',
                CFBundleName='Carry', CFBundleDisplayName='Carry', CFBundlePackageType='APPL',
                CFBundleShortVersionString='0.1.0', CFBundleVersion='7',
                LSMinimumSystemVersion='14.0', NSHighResolutionCapable=True), out)
        fingerprints = {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for folder in ('src/carry', 'macos') for p in sorted((root / folder).rglob('*'))
                        if p.is_file() and p.suffix in ('.py', '.swift')}
        (resources / 'build.json').write_text(json.dumps(dict(python=info['version'], architecture='arm64',
            runtime='explicit standalone CPython', shell='SwiftUI', distribution='unsigned internal pilot',
            view_test_runner=args.view_tests, github_cli=bool(args.gh_binary), ollama=bool(args.ollama_resources), source_sha256=fingerprints), indent=2))
        # Validate stdlib SQLite and core imports without the checkout or a PATH Python.
        subprocess.run([str(python), '-I', '-B', '-c', 'import sqlite3, carry.desktop, carry.mcp_server; assert sqlite3.connect(":memory:").execute("select sqlite_version()").fetchone()'], cwd=temp, env=env, check=True)
        shutil.copytree(app, destination, symlinks=True, copy_function=clone_copy)
    print(destination)


if __name__ == '__main__':
    main()

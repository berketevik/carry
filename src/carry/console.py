"""Carry reads and writes UTF-8 on stdio on every platform.

Clients send UTF-8 JSON on pipes; on Windows Python would decode it with the ANSI code page.
Streams that are already UTF-8 (macOS, Linux) are left untouched.
"""
import sys


def use_utf8():
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        encoding = (getattr(stream, 'encoding', None) or '').lower().replace('_', '-')
        if hasattr(stream, 'reconfigure') and encoding not in ('utf-8', 'utf8'):
            stream.reconfigure(encoding='utf-8', errors=stream.errors)

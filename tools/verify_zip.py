"""Check source ZIP safety, expected project structure, exclusions, and CRC.
Usage: python tools/verify_zip.py AutoVideoStudio-source.zip
"""
from __future__ import annotations
import argparse
from pathlib import PurePosixPath
import re
import sys
import zipfile

REQUIRED = {
    'AutoVideoStudio/README.md', 'AutoVideoStudio/requirements.txt',
    'AutoVideoStudio/main.py', 'AutoVideoStudio/launch_windows.bat',
    'AutoVideoStudio/run_tests_windows.bat', 'AutoVideoStudio/autovideo/__init__.py',
    'AutoVideoStudio/autovideo/engine.py', 'AutoVideoStudio/autovideo/gui.py',
    'AutoVideoStudio/tests/__init__.py', 'AutoVideoStudio/tests/support.py',
    'AutoVideoStudio/tests/test_engine.py', 'AutoVideoStudio/tests/test_integration.py',
    'AutoVideoStudio/tools/verify_zip.py',
    'AutoVideoStudio/tools/ffmpeg/bin/PUT_FFMPEG_HERE.txt',
}
FORBIDDEN_PARTS = {'__pycache__', '.venv', 'venv', 'node_modules', '.git'}
FORBIDDEN_SUFFIXES = {'.pyc', '.pyo', '.mp4', '.mov', '.mkv', '.mp3', '.wav', '.exe', '.dll', '.zip'}


def inspect_archive(filename: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    try:
        with zipfile.ZipFile(filename) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                errors.append('duplicate ZIP members')
            for name in names:
                path = PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts or re.match(r'^[A-Za-z]:', name):
                    errors.append(f'unsafe ZIP path: {name}')
                if any(part in FORBIDDEN_PARTS for part in path.parts) or path.suffix.lower() in FORBIDDEN_SUFFIXES:
                    errors.append(f'forbidden generated/binary file in project ZIP: {name}')
            missing = sorted(REQUIRED - set(names))
            if missing:
                errors.append('required files missing: ' + ', '.join(missing))
            bad = archive.testzip()
            if bad:
                errors.append(f'CRC/integrity failure: {bad}')
            if not names:
                errors.append('ZIP is empty')
            return names, errors
    except (OSError, zipfile.BadZipFile) as exc:
        return [], [f'cannot read ZIP: {type(exc).__name__}: {exc}']


def main() -> int:
    parser = argparse.ArgumentParser(description='Verify AutoVideo Studio source ZIP')
    parser.add_argument('zipfile', help='ZIP archive to check')
    args = parser.parse_args()
    names, errors = inspect_archive(args.zipfile)
    if errors:
        print('ZIP CHECK FAILED:')
        for error in errors:
            print(' - ' + error)
        return 1
    print(f'ZIP CHECK PASSED: {len(names)} members; all required files present; CRC/integrity OK; no unsafe paths or excluded generated media/builds.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

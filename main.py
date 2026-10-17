"""Launch GUI or run a headless dependency check."""
from __future__ import annotations
import argparse
import sys
from autovideo import __version__
from autovideo.engine import RenderError, Renderer, find_tools


def main() -> int:
    parser = argparse.ArgumentParser(description='AutoVideo Studio — offline Windows video assembly')
    parser.add_argument('--check', action='store_true', help='check FFmpeg/FFprobe and required encoders without opening GUI')
    parser.add_argument('--ffmpeg-dir', help='bin folder used by --check')
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        print('Install Python 3.12 or later (with Tcl/Tk).')
        return 1
    if args.check:
        try:
            tools = find_tools(args.ffmpeg_dir)
            Renderer(tools).check_tools()
            print(f'AutoVideo Studio {__version__}: FFmpeg/FFprobe checks PASSED; libx264 and AAC available.')
            print(f'FFmpeg: {tools.ffmpeg}\nFFprobe: {tools.ffprobe}')
            return 0
        except RenderError as exc:
            print(f'Dependency check FAILED: {exc}', file=sys.stderr)
            return 1
    try:
        from autovideo.gui import launch
        launch()
        return 0
    except ImportError as exc:
        print(f'Cannot load the desktop interface ({type(exc).__name__}). Install Python with Tcl/Tk support. See README.md.', file=sys.stderr)
        return 1
    except Exception as exc:
        print(f'Cannot start the desktop interface ({type(exc).__name__}). A Windows desktop and Python with Tcl/Tk are required.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())

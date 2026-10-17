"""Small sequential FFmpeg pipeline; no GUI or third-party Python dependencies.

Only normalized temporary files are replaced. The destination is published using
an atomic no-replace operation after FFprobe AND full decode verification.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from typing import Callable

FPS = 24
VIDEO_EXT = {'.mp4', '.mov', '.mkv', '.webm', '.avi', '.m4v', '.ts', '.mts', '.m2ts', '.wmv', '.mpg', '.mpeg'}
IMAGE_EXT = {'.png', '.jpg', '.jpeg', '.bmp', '.webp'}
AUDIO_EXT = {'.wav', '.mp3', '.m4a', '.aac', '.flac', '.ogg', '.opus', '.wma', '.mp4'}
SIZES = {'landscape': (1280, 720), 'vertical': (720, 1280)}
NO_WINDOW = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


class RenderError(Exception):
    """A safe, actionable error for presentation to the user."""


class Cancelled(RenderError):
    pass


@dataclass(frozen=True)
class MediaItem:
    path: Path
    kind: str  # video or image; list order is the exact assembly order


@dataclass(frozen=True)
class RenderOptions:
    items: tuple[MediaItem, ...]
    narration: Path | None
    output: Path
    music: Path | None = None
    aspect: str = 'landscape'
    image_seconds: float = 5.0
    music_volume: float = 0.22


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    codec: str = ''
    frames: str = ''


@dataclass(frozen=True)
class Tools:
    ffmpeg: str
    ffprobe: str


def find_tools(folder: str | Path | None = None) -> Tools:
    """An explicit folder is authoritative; otherwise bundled folder then PATH."""
    suffix = '.exe' if os.name == 'nt' else ''
    root = Path(__file__).resolve().parent.parent
    if folder:
        candidates = [Path(folder)]
    else:
        candidates = [root / 'tools' / 'ffmpeg' / 'bin', root / 'tools']
    for candidate in candidates:
        a, b = candidate / ('ffmpeg' + suffix), candidate / ('ffprobe' + suffix)
        if a.is_file() and b.is_file():
            return Tools(str(a.resolve()), str(b.resolve()))
    if not folder:
        a, b = shutil.which('ffmpeg'), shutil.which('ffprobe')
        if a and b:
            return Tools(a, b)
    raise RenderError('FFmpeg and FFprobe were not both found. Select their bin folder, '
                      'put both executables in tools/ffmpeg/bin, or add that bin folder to PATH. See README.md.')


def output_path(folder: str | Path, filename: str) -> Path:
    """Windows filename rules also enforced when tests run on another OS."""
    if not filename or filename.casefold() == '.mp4' or filename != filename.strip() or filename.endswith(('.', ' ')):
        raise RenderError('Enter a filename without leading/trailing spaces or a trailing dot.')
    if re.search(r'[<>:"/\\|?*\x00-\x1f]', filename):
        raise RenderError('The output filename contains characters Windows does not allow.')
    first = filename.split('.')[0].upper()
    if first in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9¹²³]', first):
        raise RenderError('That filename is reserved by Windows. Choose a different name.')
    if not Path(filename).suffix:
        filename += '.mp4'
    if Path(filename).suffix.lower() != '.mp4' or not Path(filename).stem.strip('.'):
        raise RenderError('Output must have a non-empty name and the .mp4 extension.')
    if len(filename) > 160:
        raise RenderError('Use a filename of 160 characters or fewer.')
    return Path(folder).expanduser().resolve() / filename


def _readable(path: Path, extensions: set[str], description: str) -> None:
    if path.suffix.lower() not in extensions:
        raise RenderError(f'Unsupported {description} extension: {path.suffix or "(none)"}. See the supported-format table in README.md.')
    try:
        if not path.is_file():
            raise OSError('not a file')
        with path.open('rb') as stream:
            if not stream.read(1):
                raise OSError('empty file')
    except OSError as exc:
        raise RenderError(f'The selected {description} is missing, empty or unreadable: {path.name}') from exc


def validate_options(options: RenderOptions) -> None:
    if not options.items:
        raise RenderError('Add at least one video or image.')
    if options.aspect not in SIZES:
        raise RenderError('Choose landscape or vertical output.')
    if not math.isfinite(options.image_seconds) or not 0.1 <= options.image_seconds <= 3600:
        raise RenderError('Image duration must be between 0.1 and 3600 seconds.')
    if not math.isfinite(options.music_volume) or not 0 <= options.music_volume <= 0.5:
        raise RenderError('Music volume must be between 0 and 50 percent.')
    for item in options.items:
        if item.kind not in {'image', 'video'}:
            raise RenderError('Unknown media type. Remove and re-add the item.')
        _readable(item.path, IMAGE_EXT if item.kind == 'image' else VIDEO_EXT, item.kind)
    if options.narration:
        _readable(options.narration, AUDIO_EXT, 'narration')
    if options.music:
        _readable(options.music, AUDIO_EXT, 'music')
    out = options.output
    output_path(out.parent, out.name)
    if out.suffix.lower() != '.mp4':
        raise RenderError('The output path must end in .mp4. Use output_path to construct it.')
    if os.path.lexists(out):
        raise RenderError('The output already exists. Choose a new filename; existing files are never overwritten.')
    if not out.parent.is_dir():
        raise RenderError('The destination folder does not exist. Choose an existing folder.')
    try:
        fd, test = tempfile.mkstemp(prefix='.autovideo-write-test-', dir=out.parent)
        os.close(fd)
        Path(test).unlink()
    except OSError as exc:
        raise RenderError('The destination folder is not writable. Choose another folder.') from exc


def ensure_space(folder: Path, seconds: float) -> int:
    # Three possible video copies (segments, concatenation, final) at capped
    # 4 Mb/s, plus audio and overhead. An estimate, not a guarantee.
    needed = int(max(0, seconds) * 2_000_000 + 256 * 1024 * 1024)
    try:
        free = shutil.disk_usage(folder).free
    except OSError as exc:
        raise RenderError('Cannot check free space in the destination folder.') from exc
    if free < needed:
        raise RenderError(f'Insufficient estimated disk space. Need approximately {needed / 1024**2:.0f} MB free; '
                          f'{free / 1024**2:.0f} MB is available. Temporary files are stored beside the output.')
    return needed


def publish_no_replace(staged: Path, destination: Path) -> None:
    try:
        if os.name == 'nt':
            os.rename(staged, destination)  # Windows rename fails if destination exists
        else:
            os.link(staged, destination)  # atomic EEXIST, unlike POSIX rename
            staged.unlink()
    except FileExistsError as exc:
        raise RenderError('The output filename became occupied during rendering. Nothing was overwritten; choose another name.') from exc
    except OSError as exc:
        raise RenderError('Cannot publish the verified output. Check folder permissions and free disk space; nothing was overwritten.') from exc


def make_logger() -> logging.Logger:
    logger = logging.getLogger('autovideo')
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    try:
        base = Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'AutoVideoStudio' / 'logs'
        base.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(base / 'app.log', maxBytes=256_000, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())
    return logger


def progress_seconds(line: str) -> float | None:
    key, sep, value = line.strip().partition('=')
    if sep and key in {'out_time_us', 'out_time_ms'}:
        # Despite the legacy name, FFmpeg out_time_ms also counts microseconds.
        try:
            result = float(value) / 1_000_000
            return result if math.isfinite(result) and result >= 0 else None
        except ValueError:
            return None
    return None


class Renderer:
    """One job per instance. Call render on a worker thread; cancel is thread-safe.

    Events: {'status': str, 'message': str, 'progress': float | None}.
    Progress is stage-local FFmpeg output time / expected stage time, not a
    fictional wall-clock or overall percentage. Only completed means success.
    """
    def __init__(self, tools: Tools, callback: Callable[[dict], None] | None = None,
                 logger: logging.Logger | None = None):
        self.tools = tools
        self.callback = callback or (lambda event: None)
        self.logger = logger or logging.getLogger('autovideo.null')
        self.cancel_event = threading.Event()
        self.status = 'ready'
        self._lock = threading.Lock()
        self._used = False

    def cancel(self) -> None:
        self.cancel_event.set()

    def _check_cancel(self) -> None:
        if self.cancel_event.is_set():
            raise Cancelled('Rendering cancelled. Source files were not changed; temporary files were removed.')

    def _event(self, message: str, progress: float | None = None) -> None:
        self.callback({'status': self.status, 'message': message, 'progress': progress})

    @staticmethod
    def _stop(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

    def _capture(self, command: list[str], timeout: float = 30) -> str:
        self._check_cancel()
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            try:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr,
                                        creationflags=NO_WINDOW)
            except OSError as exc:
                raise RenderError('Cannot launch FFmpeg/FFprobe. Check that the selected executables run on this computer.') from exc
            start = time.monotonic()
            try:
                while proc.poll() is None:
                    self._check_cancel()
                    if time.monotonic() - start > timeout:
                        raise RenderError('Media inspection timed out. Check the file and copy network files to a local drive.')
                    time.sleep(0.05)
                self._check_cancel()
                if proc.returncode:
                    self.logger.warning('inspection failed; exit=%s', proc.returncode)
                    raise RenderError('FFmpeg/FFprobe could not inspect this media or tool installation. The file may be damaged or use an unsupported codec.')
                stdout.seek(0)
                raw = stdout.read(16 * 1024 * 1024 + 1)
                if len(raw) > 16 * 1024 * 1024:
                    raise RenderError('Inspection output exceeded the safety limit.')
                return raw.decode('utf-8', errors='replace')
            finally:
                self._stop(proc)

    def check_tools(self) -> None:
        self._capture([self.tools.ffprobe, '-version'])
        encoders = self._capture([self.tools.ffmpeg, '-hide_banner', '-encoders'])
        for required in ('libx264', 'aac'):
            if not re.search(r'\b' + required + r'\b', encoders):
                raise RenderError(f'This FFmpeg build lacks the required {required} encoder. Use a full Windows build (see README).')

    def probe(self, path: Path) -> MediaInfo:
        try:
            data = json.loads(self._capture([
                self.tools.ffprobe, '-v', 'error', '-show_entries',
                'stream=codec_type,codec_name,width,height,duration,nb_frames:format=duration', '-of', 'json', str(path.resolve())]))
            streams = data.get('streams', [])
            video = next((s for s in streams if s.get('codec_type') == 'video'), {})
            audio = next((s for s in streams if s.get('codec_type') == 'audio'), {})
            duration = float(data.get('format', {}).get('duration', 0) or 0)
            if not math.isfinite(duration) or duration < 0:
                duration = 0
            return MediaInfo(duration, bool(video), bool(audio), int(video.get('width', 0)), int(video.get('height', 0)),
                             str(video.get('codec_name', '')), str(video.get('nb_frames', '')))
        except (ValueError, TypeError, KeyError) as exc:
            raise RenderError(f'Unreadable media inspection data for {path.name}.') from exc

    def _ffmpeg(self, args: list[str], workspace: Path, label: str, duration: float | None) -> None:
        self._check_cancel()
        self._event(label + ' — progress unavailable')
        self.logger.info('stage=%s', label)
        progress_file, error_file = workspace / 'progress.txt', workspace / 'ffmpeg-error.txt'
        # Only private files in this unique job workspace are truncated.
        with progress_file.open('wb') as progress_out, error_file.open('wb') as errors:
            command = [self.tools.ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-xerror',
                       '-progress', 'pipe:1', '-nostats', '-filter_threads', '1', '-filter_complex_threads', '1'] + args
            try:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=progress_out, stderr=errors,
                                        creationflags=NO_WINDOW)
            except OSError as exc:
                raise RenderError('FFmpeg could not be started. Recheck the FFmpeg folder.') from exc
            try:
                with progress_file.open('r', encoding='utf-8', errors='replace') as reader:
                    while True:
                        self._check_cancel()
                        for line in reader:
                            elapsed = progress_seconds(line)
                            if elapsed is not None and duration and duration > 0:
                                self._event(label, min(100.0, elapsed / duration * 100.0))
                        if proc.poll() is not None:
                            break
                        time.sleep(0.12)
                self._check_cancel()
                if proc.returncode:
                    errors.flush()
                    # Do not log commands, user paths, embedded metadata, or media contents.
                    text = error_file.read_text(encoding='utf-8', errors='replace')[-12000:]
                    hint = 'Input may be corrupt or unsupported. Try the item in VLC and check free disk space.'
                    for signature, explanation in {
                        'No space left on device': 'The destination drive ran out of disk space.',
                        'Permission denied': 'File access was denied. Check file and folder permissions.',
                        'Unknown encoder': 'The FFmpeg build lacks a required encoder.',
                        'Invalid data found': 'The input is invalid, damaged, or not supported by this FFmpeg build.',
                        'Error while opening encoder': 'The video encoder could not start. Check the FFmpeg installation.',
                    }.items():
                        if signature.lower() in text.lower():
                            hint = explanation
                            break
                    self.logger.warning('FFmpeg failed; stage=%s exit=%s', label, proc.returncode)
                    raise RenderError(f'FFmpeg failed during {label} (exit {proc.returncode}). {hint}')
            finally:
                self._stop(proc)

    @staticmethod
    def _encoding() -> list[str]:
        return ['-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '23', '-maxrate', '4000k',
                '-bufsize', '8000k', '-threads', '2', '-pix_fmt', 'yuv420p']

    def verify_output(self, path: Path, workspace: Path, expected: float, size: tuple[int, int]) -> MediaInfo:
        if not path.is_file() or path.stat().st_size == 0:
            raise RenderError('Rendering produced no usable output file.')
        self._event('Verifying MP4 structure — progress unavailable')
        info = self.probe(path)
        if (not info.has_video or info.duration <= 0 or
                (info.width, info.height) != size or abs(info.duration - expected) > max(0.5, expected * 0.01)):
            raise RenderError('Output verification failed: missing streams, wrong size, or unexpected duration.')
        maps = ['-map', '0:v:0'] + (['-map', '0:a:0'] if info.has_audio else [])
        self._ffmpeg(['-i', str(path), *maps, '-threads', '2', '-f', 'null', '-'],
                     workspace, 'Verifying full video and audio decode', expected)
        return info

    def render(self, options: RenderOptions) -> Path:
        with self._lock:
            if self._used:
                raise RenderError('Each Renderer instance can run only one job.')
            self._used = True
            self.status = 'running'
        self._event('Validating inputs — progress unavailable')
        try:
            self._check_cancel()
            validate_options(options)
            self.check_tools()
            infos = []
            for index, item in enumerate(options.items, 1):
                self._event(f'Inspecting visual {index}/{len(options.items)} — progress unavailable')
                info = self.probe(item.path)
                if not info.has_video or info.width <= 0 or info.height <= 0:
                    raise RenderError(f'Visual {index} has no readable video/image stream.')
                if item.kind == 'video' and info.duration <= 0:
                    raise RenderError(f'Cannot determine duration of video {index}. Convert it to an ordinary MP4 first.')
                if item.kind == 'image':
                    if info.codec == 'apng' or (info.frames.isdigit() and int(info.frames) > 1):
                        raise RenderError('Animated images are not supported. Supply a static image or a video instead.')
                    # A WebP ANIM/ANMF chunk means animation even if FFprobe reports no frame count.
                    if item.path.suffix.lower() == '.webp':
                        with item.path.open('rb') as image:
                            header = image.read(12)
                            if header[:4] != b'RIFF' or header[8:12] != b'WEBP':
                                raise RenderError('Invalid WebP image container.')
                            while True:
                                chunk = image.read(8)
                                if len(chunk) < 8:
                                    break
                                if chunk[:4] in {b'ANIM', b'ANMF'}:
                                    raise RenderError('Animated WebP is not supported. Supply a static image or video.')
                                length = int.from_bytes(chunk[4:], 'little')
                                image.seek(length + (length % 2), 1)
                infos.append(info)
            narration = self.probe(options.narration) if options.narration else None
            if narration and (not narration.has_audio or narration.duration <= 0):
                raise RenderError('Narration must contain readable audio with a known positive duration.')
            if options.music:
                music = self.probe(options.music)
                if not music.has_audio or music.duration <= 0:
                    raise RenderError('Background music must contain readable audio with a known positive duration.')
            estimate = max(sum(options.image_seconds if i.kind == 'image' else m.duration
                               for i, m in zip(options.items, infos)), narration.duration if narration else 0,
                           music.duration if options.music else 0)
            ensure_space(options.output.parent, estimate)
            size = SIZES[options.aspect]
            width, height = size
            vf = (f'scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,'
                  f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={FPS}')
            with tempfile.TemporaryDirectory(prefix='.autovideo-', dir=options.output.parent) as directory:
                workspace = Path(directory)
                total_visual = 0.0
                audio_clips = []
                timeline_offset = 0.0
                for n, (item, info) in enumerate(zip(options.items, infos)):
                    self._check_cancel()
                    segment = workspace / f'segment{n:05d}.mp4'
                    duration = options.image_seconds if item.kind == 'image' else info.duration
                    if item.kind == 'video' and info.has_audio:
                        audio_clips.append((item.path, timeline_offset, duration))
                    args = []
                    if item.kind == 'image':
                        args += ['-loop', '1', '-framerate', str(FPS)]
                    args += ['-threads', '2', '-i', str(item.path.resolve()), '-map', '0:v:0', '-an', '-sn', '-dn']
                    if item.kind == 'image':
                        args += ['-t', f'{duration:.6f}']
                    args += ['-vf', vf, '-map_metadata', '-1'] + self._encoding() + ['-y', str(segment)]
                    self._ffmpeg(args, workspace, f'Preparing visual {n+1}/{len(options.items)}', duration)
                    normalized = self.probe(segment)
                    if not normalized.has_video or normalized.duration <= 0:
                        raise RenderError(f'Visual {n+1} could not be normalized; no media was skipped.')
                    total_visual += normalized.duration
                    timeline_offset += normalized.duration
                concat = workspace / 'segments.txt'
                concat.write_text(''.join(f"file 'segment{n:05d}.mp4'\n" for n in range(len(options.items))), encoding='ascii')
                joined = workspace / 'joined.mp4'
                self._ffmpeg(['-f', 'concat', '-safe', '1', '-i', str(concat), '-map', '0:v:0',
                              '-c:v', 'copy', '-an', '-map_metadata', '-1', '-y', str(joined)],
                             workspace, 'Joining prepared visuals', total_visual)
                total = max(total_visual, narration.duration if narration else 0)
                ensure_space(options.output.parent, total)  # conservative second check
                staged = workspace / 'verified-output.mp4'
                args = ['-threads', '2', '-i', str(joined)]
                audio_filters = []
                audio_labels = []
                next_input = 1
                if options.narration:
                    args += ['-i', str(options.narration.resolve())]
                    audio_filters.append(f'[{next_input}:a:0]aresample=48000,aformat=channel_layouts=stereo,apad,atrim=duration={total:.6f},asetpts=PTS-STARTPTS[narration]')
                    audio_labels.append('[narration]')
                    next_input += 1
                if options.music:
                    args += ['-stream_loop', '-1', '-i', str(options.music.resolve())]
                    audio_filters.append(f'[{next_input}:a:0]aresample=48000,aformat=channel_layouts=stereo,volume={options.music_volume:.6f},apad,atrim=duration={total:.6f},asetpts=PTS-STARTPTS[music]')
                    audio_labels.append('[music]')
                    next_input += 1
                for clip_index, (clip_path, offset, duration) in enumerate(audio_clips):
                    args += ['-i', str(clip_path.resolve())]
                    label = f'clipaudio{clip_index}'
                    audio_filters.append(f'[{next_input}:a:0]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={duration:.6f},asetpts=PTS-STARTPTS,adelay={round(offset * 1000)}:all=1,apad,atrim=duration={total:.6f}[{label}]')
                    audio_labels.append(f'[{label}]')
                    next_input += 1
                if audio_labels:
                    audio = ';'.join(audio_filters) + ';' + ''.join(audio_labels) + f'amix=inputs={len(audio_labels)}:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95:level=0:latency=1[aout]'
                    args += ['-filter_complex', audio, '-map', '0:v:0', '-map', '[aout]']
                else:
                    args += ['-map', '0:v:0', '-an']
                args += [
                         '-vf', f'tpad=stop_mode=clone:stop_duration={max(0, total-total_visual)+0.1:.6f}',
                         '-t', f'{total:.6f}', '-map_metadata', '-1']
                args += self._encoding() + ['-c:a', 'aac', '-b:a', '128k', '-ar', '48000',
                                           '-movflags', '+faststart', '-y', str(staged)]
                self._ffmpeg(args, workspace, 'Encoding final MP4', total)
                self.verify_output(staged, workspace, total, size)
                self._check_cancel()  # publishing is the short, non-cancellable commit point
                publish_no_replace(staged, options.output)
            self.status = 'completed'
            self.logger.info('job completed; verification=structure+full-decode')
            self._event('Completed — output verified', 100.0)
            return options.output
        except Cancelled:
            self.status = 'cancelled'
            self.logger.info('job cancelled')
            self._event('Cancelled — no output was published')
            raise
        except Exception as exc:
            self.status = 'failed'
            self.logger.error('job failed; exception_type=%s', type(exc).__name__)
            self._event('Failed — no success notification was issued')
            if isinstance(exc, RenderError):
                raise
            if isinstance(exc, OSError):
                raise RenderError('File operation failed. Check permissions, free space, and that source files remain available.') from exc
            raise RenderError('Unexpected application error. Restart the app and consult the diagnostic log (exception type recorded).') from exc

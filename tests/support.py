"""Tiny fixtures generated locally; no downloads and no third-party packages."""
import math
from pathlib import Path
import struct
import subprocess
import wave
import zlib


def png(path: Path, width=48, height=32, color=(0, 255, 0)):
    def chunk(kind, payload):
        return struct.pack('!I', len(payload)) + kind + payload + struct.pack('!I', zlib.crc32(kind + payload) & 0xffffffff)
    data = b''.join(b'\0' + bytes(color) * width for _ in range(height))
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('!2I5B', width, height, 8, 2, 0, 0, 0))
                     + chunk(b'IDAT', zlib.compress(data)) + chunk(b'IEND', b''))


def tone(path: Path, seconds, frequency=440, amplitude=0.2):
    rate = 48000
    with wave.open(str(path), 'wb') as audio:
        audio.setparams((1, 2, rate, 0, 'NONE', 'not compressed'))
        audio.writeframes(b''.join(struct.pack('<h', int(32767 * amplitude * math.sin(2 * math.pi * frequency * n / rate)))
                                   for n in range(round(seconds * rate))))


def command(args, timeout=40):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise AssertionError(f'Fixture/inspection command failed ({result.returncode}): {result.stderr.decode(errors="replace")[-2000:]}')
    return result.stdout


def clip(ffmpeg, path, color, seconds=0.5, with_audio=False):
    args = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-f', 'lavfi', '-i',
            f'color=c={color}:s=160x90:r=24:d={seconds}']
    if with_audio:
        args += ['-f', 'lavfi', '-i', f'sine=frequency=1300:sample_rate=48000:duration={seconds}', '-c:a', 'aac', '-shortest']
    args += ['-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1', '-pix_fmt', 'yuv420p', '-y', str(path)]
    command(args)


def frame(ffmpeg, path, seconds):
    data = command([ffmpeg, '-v', 'error', '-ss', str(seconds), '-i', str(path), '-frames:v', '1',
                    '-vf', 'scale=16:16', '-threads', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    if len(data) != 16 * 16 * 3:
        raise AssertionError('No readable frame at requested timestamp')
    return [tuple(data[n:n+3]) for n in range(0, len(data), 3)]


def samples(ffmpeg, path, start, seconds=0.1):
    raw = command([ffmpeg, '-v', 'error', '-ss', str(start), '-i', str(path), '-t', str(seconds),
                   '-vn', '-ac', '1', '-ar', '48000', '-f', 's16le', '-'])
    return [n[0] / 32768 for n in struct.iter_unpack('<h', raw)]


def magnitude(data, frequency):
    if not data:
        return 0
    real = sum(x * math.cos(2 * math.pi * frequency * n / 48000) for n, x in enumerate(data))
    imag = sum(x * math.sin(2 * math.pi * frequency * n / 48000) for n, x in enumerate(data))
    return 2 * math.hypot(real, imag) / len(data)

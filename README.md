# AutoVideo Studio — Phase 1

A small offline Windows desktop app that assembles your selected clips and still images in order, optionally adds narration and background music, and produces an MP4. It uses Python's built-in Tkinter interface and FFmpeg's CPU encoders. **No accounts, paid services, cloud processing, GPU, Python packages, or API keys are required.**

## Quick start (Windows 10)

1. Download the AutoVideo Studio ZIP and extract the whole `AutoVideoStudio` folder somewhere writable, such as `Documents`. Do not run it from inside the ZIP.
2. Install **64-bit Python 3.12 or newer for Windows** from [python.org/downloads/windows](https://www.python.org/downloads/windows/). In the installer leave **Tcl/Tk and IDLE** selected and choose the Python Launcher. You do not need to install packages with pip.
3. Install FFmpeg as described in [Install FFmpeg](#install-ffmpeg).
4. Double-click `launch_windows.bat`. The app opens a small desktop window. The first render should be tried with short, local media and a new output name.

If double-clicking the batch file closes quickly, open Command Prompt in the extracted folder and run:

```text
py -3.12 main.py
```

To test FFmpeg/FFprobe without opening the window:

```text
py -3.12 main.py --check
```

## Install FFmpeg

FFmpeg and FFprobe are required external programs, but are free and open source. **They are not bundled in this source ZIP.** Obtain a Windows build from the [FFmpeg download page](https://ffmpeg.org/download.html) (Windows EXE builds link to the Windows build providers); select a full build containing both `ffmpeg.exe` and `ffprobe.exe` and the **libx264 (H.264) and AAC encoders**.

Use either option:

- **Portable, no PATH changes:** extract FFmpeg, then in AutoVideo Studio choose the folder containing both executables using the **FFmpeg bin folder → Browse** button. A typical extracted location ends with `\bin`. Or copy `ffmpeg.exe` and `ffprobe.exe` into `AutoVideoStudio\tools\ffmpeg\bin`.
- **PATH:** add the folder containing both executables to the Windows PATH, then close and reopen the app. The app searches PATH automatically.

You can confirm discovery using `py -3.12 main.py --check` from Command Prompt after adding FFmpeg to PATH. A successful check reports the FFmpeg and FFprobe paths and confirms both required encoders. If it fails, select the correct bin folder inside the app. Do not download individual DLLs from unrelated sites.

## Use the application

1. **Add videos** and/or **Add images**. The on-screen list is the actual sequence; choose an entry and use **Up**, **Down**, or **Remove** to arrange it. Original input files are never changed.
2. Optionally choose narration and background music.
3. Select an existing output folder, a new `.mp4` filename, and landscape (16:9) or vertical (9:16). If the name omits `.mp4`, the app adds it.
4. Optionally set transitions (section 02), timed text overlay (section 04), and audio ducking (section 02 Audio Mix). Set image duration and optional music gain, then press **Render video**. The app reports each step; stage progress, when available, is derived from FFmpeg's output timestamps and is only for the current step—not an invented whole-job percentage. Otherwise it says progress is unavailable.
5. The app reports completion and shows the result **only after** checking MP4 streams, dimensions and duration and decoding all available streams. **Preview Video · default player** opens that verified MP4 in the Windows default video player; playback controls are provided by that player. **Show verified output** opens Explorer with the file selected. Existing output names are never overwritten.
6. To request a supported settings change, enter one command in **Supported Editing Commands**, review the proposed settings and next output filename, then click **Apply Changes**. Applying a proposal does not render automatically; review the controls and press **Render video** when ready. The prior render remains untouched.

### GUI layout and scrolling

The window divides controls into numbered sections inside a vertically scrollable area. Scroll sections 01–05 with the mouse wheel when the content is taller than the window; the footer stays fixed at the bottom and is always visible.

- **01 Visual Sequence** — add videos and images, then arrange with Up, Down, or Remove.
- **02 Visual Transitions** — choose cut / crossfade / fade-to-black and duration; applied between every visual gap.
- **02 Audio Mix** — narration, background music, music gain (0–50%, default 22%), and auto-ducking controls (shares the lower row with Output).
- **03 Output** — save folder, filename, FFmpeg bin folder, frame aspect (landscape 16:9 or vertical 9:16), still image duration.
- **04 Timed Text Overlay** — text, start/end seconds, font size, colour, position (top / centre / bottom), optional background box.
- **05 Supported Editing Commands** — type one settings request, review the proposal, then Apply Changes.

The footer contains Render video, Cancel job, Preview Video (opens the verified MP4 in the default player), Show verified output (opens Explorer), the progress bar, and the status line.

### Supported editing commands

The local command parser changes only existing GUI settings. It does not use AI or edit video pixels, speech, avatar appearance, or a timeline. Enter one request at a time:

- `Make the background music louder` or `Increase music volume` raises music gain by 5 percentage points; `Make the background music quieter` or `Reduce music volume` lowers it by 5 points.
- `Set music gain to 30%` sets a specific gain from 0% to 50%.
- `Remove the background music` clears the selected music file.
- `Change the output to vertical format` or `Change the output to landscape format` selects the frame shape.
- `Set each image to display for 7 seconds` changes the still-image duration (0.1–3600 seconds).
- `Render another version with these settings` keeps the current settings and prepares another output filename.

Unsupported, combined, ambiguous, and unsafe requests are rejected for clarification. The parser never executes prompt text or accepts file paths. Applying any proposal chooses an unused output name when the current name already exists (for example, `my-video-v2.mp4`).

Cancel stops the current FFmpeg process, removes temporary files, and does not publish a partial render. Closing while a job runs prompts to cancel it. If rendering is unexpectedly interrupted (for example, Windows shuts down), a `.autovideo-*` temporary folder may remain beside the destination; only remove such a folder when no job is running.

## Exact Phase 1 assembly behavior

- Visuals play in the selected order. **Still-image duration defaults to 5 seconds per image**, configurable in the window from 0.1 seconds to 1 hour.
- Output is 1280×720 landscape or 720×1280 vertical, 24 fps. Visuals are **fit and letter/pillar-boxed with black bars**; they are not cropped. Output uses H.264 (`libx264`, ultrafast preset, CRF 23, 2 threads, capped at 4 Mbit/s) and AAC at 128 kbit/s / 48 kHz. This is CPU-only and favors modest rendering cost over maximum quality. Long/high-resolution inputs can take a while, especially on an older computer.
- Original audio in video clips is included at the clip's position in the sequence. Still images have no audio. Narration is optional, begins at the start, and is padded with silence when visuals are longer; if narration is longer than visuals, the final visual frame is held. A render with no available audio source is a valid silent video. All selected visuals are used or the render fails—there is no script understanding, semantic selection, trimming UI, per-clip volume control, or automatic synchronization. Visual transitions and timed text overlays are supported; see below.
- Optional music loops to the final duration, defaults to **22% linear volume** (user-adjustable 0–50%), and is mixed with narration and clip audio through a limiter. Optional **audio ducking** automatically reduces music during detected narration speech. Ducking is off by default; strength is the music level kept during speech (0.05–0.9, engine default 0.25) and the speech threshold is a linear level (0.005–0.5, engine default 0.02). Ducking requires both music and narration and leaves narration, clip audio, and the limiter path untouched. The gain is not a promise about perceived loudness; check the exported sound. There is no per-clip volume control.
- **Visual transitions** are applied between every visual gap. Supply one transition per gap (N−1 entries for N items; use 'cut' for a hard cut), or none for plain cuts. Types: **cut** (instant switch), **crossfade** (blends frames across the overlap), **fade-to-black** (dips through black). Crossfade and fade-to-black durations are user-entered in seconds, constrained to 0.1–5.0 s and must be shorter than each neighbouring visual (a neighbour-length cap; the render fails otherwise). A crossfade overlaps adjoining visuals and shortens the joined timeline by the overlap; fade-to-black inserts black and adds its duration; a cut concatenates. Plain cuts join with a stream-copy concat; any crossfade or fade-to-black re-encodes the joined visuals.
- **Timed text overlays** burn one line of text onto the video between a start and end time (FFmpeg time). Set the text (non-empty, 200 characters or fewer), font size (8–200), hex colour (e.g. #FFFFFF), position (top / centre / bottom), and optional black background box. Start must be non-negative and end must be after start. Overlays entirely beyond the video length are skipped; the end is clamped to the video duration. Enable the overlay in the GUI before rendering.
- **Audio mixing** combines up to three sources: original clip audio kept at each clip's sequence position, optional narration beginning at the start, and optional background music looping to the final duration. All sources pass through a limiter and are encoded as AAC 128 kbit/s at 48 kHz. When ducking is on, only the music bus is reduced during detected speech; narration and clip audio are not attenuated.

- FFmpeg decodes inputs and may support many codecs inside the listed containers, but codec/variant support depends on the installed FFmpeg build. An extension is not a guarantee that the stream is valid. Unsupported, damaged, or unusual streams fail with an error rather than being silently dropped. Animated GIF/APNG/WebP, image sequences, variable-frame-rate synchronization guarantees, and unusual multistream media are not supported. For best results, use ordinary H.264 MP4, static JPEG/PNG/WebP images, and WAV/MP3/M4A/FLAC narration or music. WebM/MKV and other formats may work if FFmpeg can decode them.
- The extension filters allow: video `.mp4 .mov .mkv .webm .avi .m4v .ts .mts .m2ts .wmv .mpg .mpeg`; static image `.png .jpg .jpeg .bmp .webp`; audio `.wav .mp3 .m4a .aac .flac .ogg .opus .wma .mp4` (an MP4 must actually contain audio). These are format candidates, not a promise that every codec variant works.
- The app estimates free disk space before starting but cannot guarantee space will remain available. Temporary media is kept on the output drive so final publication can avoid overwriting an existing file. For large sources, allow substantial free space and keep source files on a local, readable drive.
- Diagnostic log: `%LOCALAPPDATA%\AutoVideoStudio\logs\app.log` (usually `C:\Users\<you>\AppData\Local\AutoVideoStudio\logs\app.log`). It records job stages and error categories, **not** media contents, audio/video, full commands, or file paths.

## Run tests

Tests use only Python's standard library. Install FFmpeg/FFprobe with `libx264` and AAC for the real-media integration tests, then run `run_tests_windows.bat` or:

```text
py -3.12 -m unittest discover -s tests -v
```

The suite generates very small local test clips, images, and WAVs; it requires no downloads or sample media. It tests validation, errors, status, cancellation, no-overwrite publication, audio mixing/looping, visual order, landscape/vertical output, readable full-decode verification, and cleanup. If FFmpeg/FFprobe is missing, real-media tests are explicitly skipped; those skips do **not** verify rendering.

## Source layout

- `main.py` — launch and `--check` entry point.
- `autovideo/engine.py` — input checks, FFmpeg pipeline, worker/cancellation events, and output verification.
- `autovideo/gui.py` — Tkinter desktop interface and external-player preview action.
- `autovideo/editing.py`, `autovideo/preview.py` — allowlisted edit proposals, safe version naming, and preview path checks.
- `tests/` — standard-library unit and real FFmpeg integration tests.
- `tools/verify_zip.py` — ZIP structure, safety, required files, exclusions and CRC checker.
- `launch_windows.bat`, `run_tests_windows.bat` — Windows launch/test helpers.
- `requirements.txt` — records that there are no third-party Python dependencies.

## Limitations / validation

This is Phase 1, not an editor timeline or an AI creator. It does not package FFmpeg, make an installer, or promise to build on every Windows installation. The GUI requires a normal interactive Windows desktop and Python with Tcl/Tk. CPU encoding may be slow. Windows itself and its `.bat` launch scripts must be smoke-tested on Windows after extraction. See the accompanying Phase 1 handoff/report for exactly which tests were run in this delivery environment.

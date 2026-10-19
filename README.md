# AutoVideo Studio — v1.6.0

A fast, offline Windows desktop app that assembles your selected clips and still images in order, optionally adds narration, background music, timed text overlays, and visual transitions, and produces an MP4. It uses a built-in portable Python environment and FFmpeg's CPU encoders. **No installation, accounts, cloud processing, GPU, or internet connection is required.**

## Quick Start (Zero-Install)

1. Download the AutoVideo Studio ZIP and extract the whole `AutoVideoStudio` folder somewhere writable, such as `Documents` or your Desktop. Do not run it from inside the ZIP.
2.Double-click the **AutoVideo Studio.vbs** file to launch the app. 
3. The app opens instantly. No Python installation, no setup, and no black command boxes.

*(Note: If Windows Defender shows a warning when you first open the app, this is a standard security prompt for downloaded ZIP files. Click "More info" and "Run anyway".)*

## Use the Application

1. **Add videos** and/or **Add images**. The on-screen list is the actual sequence; choose an entry and use **Up**, **Down**, or **Remove** to arrange it. Original input files are never changed.
2. Optionally choose narration and background music.
3. Select an existing output folder, a new `.mp4` filename, and landscape (16:9) or vertical (9:16). If the name omits `.mp4`, the app adds it.
4. Optionally set transitions, timed text overlays, and audio ducking. Set image duration and optional music gain, then press **Render video**. 
5. The app reports completion **only after** checking MP4 streams, dimensions, and duration, and decoding all available streams. **Preview Video · default player** opens the verified MP4. Existing output names are never overwritten.
6. To request a supported settings change, enter one command in **Supported Editing Commands**, review the proposed settings, then click **Apply Changes**.

### GUI Layout and Scrolling
The window divides controls into numbered sections inside a vertically scrollable area. Scroll with the mouse wheel when the content is taller than the window; the footer stays fixed and is always visible.

- **01 Visual Sequence** — add videos and images, then arrange them.
- **02 Visual Transitions** — choose cut / crossfade / fade-to-black and duration.
- **02 Audio Mix** — narration, background music, music gain (0–50%), and auto-ducking.
- **03 Output** — save folder, filename, frame aspect, still image duration.
- **04 Timed Text Overlay** — text, start/end seconds, font size, colour, position, and optional background box.
- **05 Supported Editing Commands** — type one settings request, review, then Apply.

### Supported Editing Commands
The local command parser changes only existing GUI settings. It does not use AI or edit video pixels. Enter one request at a time:
- `Make the background music louder` / `quieter` (adjusts by 5%).
- `Set music gain to 30%` (0% to 50%).
- `Remove the background music`.
- `Change the output to vertical format` / `landscape format`.
- `Set each image to display for 7 seconds`.
- `Render another version with these settings`.

Unsupported, combined, ambiguous, and unsafe requests are rejected. The parser never executes prompt text or accepts file paths.

## Exact Assembly Behavior

- Visuals play in the selected order. Still-image duration defaults to 5 seconds per image (configurable 0.1s to 1 hour).
- Output is 1280×720 landscape or 720×1280 vertical, 24 fps. Visuals are fit and letter/pillar-boxed with black bars. Output uses H.264 (`libx264`, ultrafast preset, CRF 23, 2 threads, capped at 4 Mbit/s) and AAC at 128 kbit/s / 48 kHz. 
- Original audio in video clips is included. Narration begins at the start. Music loops to the final duration (defaults to 22% volume). 
- **Visual transitions** are applied between every visual gap (cut, crossfade, or fade-to-black). 
- **Timed text overlays** burn one line of text onto the video between a start and end time with custom font size, colour, position, and optional background box.
- **Audio ducking** automatically reduces music during detected narration speech. Ducking is off by default.

## Troubleshooting

- **The app doesn't open:** Ensure you extracted the ZIP folder completely. Do not run the app from inside the ZIP file.
- **FFmpeg errors:** The app bundles FFmpeg, but if you encounter codec issues, ensure your input media is a standard format (H.264 MP4, static JPEG/PNG, WAV/MP3).
- **Diagnostic Log:** Check `%LOCALAPPDATA%\AutoVideoStudio\logs\app.log`. It records job stages and error categories, but **not** your media contents, audio/video, or file paths.

## Source Layout (For Developers)

- `main.py` — launch and `--check` entry point.
- `autovideo/engine.py` — input checks, FFmpeg pipeline, worker/cancellation events, and output verification.
- `autovideo/gui.py` — Tkinter desktop interface.
- `autovideo/editing.py`, `autovideo/preview.py` — allowlisted edit proposals and preview path checks.
- `tests/` — standard-library unit and real FFmpeg integration tests.
- `tools/verify_zip.py` — ZIP structure and safety checker.
- `runtime/` — Portable Python environment.
- `AutoVideo Studio.vbs` — Silent background launcher.

## Third-Party Licenses & Compliance
This application bundles FFmpeg (GPLv3). The complete corresponding source code, license text, and build information are included in the `THIRD_PARTY_NOTICES` folder within this distribution.

## Limitations
This is a fast clip assembler, not a full editor timeline or an AI creator. It does not support per-clip volume control, animated images (GIF/APNG/WebP), or automatic synchronization. CPU encoding may be slow on older computers. Requires 64-bit Windows 10 or 11.

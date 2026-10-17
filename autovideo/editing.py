"""Deterministic allowlist for the GUI's supported editing requests."""
from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
import re


class EditRequestError(ValueError):
    def __init__(self, message: str, kind: str = 'unsupported'):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class EditProposal:
    summary: str
    aspect: str | None = None
    image_seconds: float | None = None
    music_volume: float | None = None
    remove_music: bool = False
    render_another_version: bool = False


def next_version_path(output: Path, protected: Path | None = None) -> Path:
    output = output.resolve()
    protected = protected.resolve() if protected else None
    if output != protected and not output.exists():
        return output
    match = re.fullmatch(r'(.*)-v([0-9]+)', output.stem, re.I)
    stem = match.group(1) if match else output.stem
    version = int(match.group(2)) + 1 if match else 2
    while True:
        candidate = output.with_name(f'{stem}-v{version}{output.suffix}')
        if candidate != protected and not candidate.exists():
            return candidate
        version += 1


def parse_edit_request(text: str, *, aspect: str, image_seconds: float,
                       music_volume: float, has_music: bool) -> EditProposal:
    request = ' '.join(text.strip().split())
    if not request:
        raise EditRequestError('Enter one supported editing request.', 'ambiguous')

    if re.search(r'\b(python|shell|command|ffmpeg|delete|erase|overwrite|source file|file path|folder path)\b|[\\/]', request, re.I):
        raise EditRequestError('This request includes a command, path, or destructive operation. Prompt text cannot run commands or change/delete files.', 'unsafe')
    if re.search(r'\b(and|then|also|or)\b', request, re.I):
        raise EditRequestError('Please enter one change at a time so each proposal is clear.', 'ambiguous')

    normalized = request.rstrip('.!?').casefold()
    if re.fullmatch(r'render another version with these settings', normalized):
        return EditProposal('Keep the current settings and prepare another output version.', render_another_version=True)

    if normalized in {'remove background music', 'remove the background music', 'turn off background music'}:
        if not has_music:
            raise EditRequestError('No background music is selected to remove.', 'ambiguous')
        return EditProposal('Remove the selected background music from the next render.', remove_music=True)

    if normalized in {'make background music louder', 'make the background music louder',
                      'increase background music', 'increase music volume'}:
        if not has_music:
            raise EditRequestError('Select a background music file before changing its gain.', 'ambiguous')
        new_volume = min(50.0, music_volume + 5.0)
        if new_volume == music_volume:
            raise EditRequestError('Music gain is already at the 50% maximum.', 'ambiguous')
        return EditProposal(f'Increase music gain from {music_volume:g}% to {new_volume:g}%.', music_volume=new_volume)

    if normalized in {'make background music quieter', 'make the background music quieter',
                      'reduce background music', 'reduce music volume'}:
        if not has_music:
            raise EditRequestError('Select a background music file before changing its gain.', 'ambiguous')
        new_volume = max(0.0, music_volume - 5.0)
        if new_volume == music_volume:
            raise EditRequestError('Music gain is already at the 0% minimum.', 'ambiguous')
        return EditProposal(f'Reduce music gain from {music_volume:g}% to {new_volume:g}%.', music_volume=new_volume)

    gain = re.fullmatch(r'set music gain to ([0-9]+(?:\.[0-9]+)?)%', normalized)
    if gain:
        if not has_music:
            raise EditRequestError('Select a background music file before changing its gain.', 'ambiguous')
        new_volume = float(gain.group(1))
        if not math.isfinite(new_volume) or not 0 <= new_volume <= 50:
            raise EditRequestError('Music gain must be between 0% and 50%.', 'unsupported')
        if new_volume == music_volume:
            raise EditRequestError(f'Music gain is already {new_volume:g}%.', 'ambiguous')
        return EditProposal(f'Set music gain from {music_volume:g}% to {new_volume:g}%.', music_volume=new_volume)

    if normalized in {'change the output to vertical format', 'change output to vertical format',
                      'set output to vertical', 'change output to vertical'}:
        if aspect == 'vertical':
            raise EditRequestError('Output is already set to vertical format.', 'ambiguous')
        return EditProposal('Change the output frame to vertical 9:16 (720×1280).', aspect='vertical')

    if normalized in {'change the output to landscape format', 'change output to landscape format',
                      'set output to landscape', 'change output to landscape'}:
        if aspect == 'landscape':
            raise EditRequestError('Output is already set to landscape format.', 'ambiguous')
        return EditProposal('Change the output frame to landscape 16:9 (1280×720).', aspect='landscape')

    duration = re.fullmatch(r'set each image to display for ([0-9]+(?:\.[0-9]+)?) seconds', normalized)
    if duration:
        new_seconds = float(duration.group(1))
        if not math.isfinite(new_seconds) or not 0.1 <= new_seconds <= 3600:
            raise EditRequestError('Image duration must be between 0.1 and 3600 seconds.', 'unsupported')
        if new_seconds == image_seconds:
            raise EditRequestError(f'Images are already set to {new_seconds:g} seconds.', 'ambiguous')
        return EditProposal(f'Set each image duration from {image_seconds:g} to {new_seconds:g} seconds.',
                            image_seconds=new_seconds)

    if re.search(r'\b(music|gain|volume|louder|quieter|vertical|landscape|image|seconds|render)\b', normalized):
        raise EditRequestError('That request is unclear or not in the supported command list. Please choose one specific change.', 'ambiguous')
    raise EditRequestError('That request is not supported. This assistant only changes the listed audio and output settings.', 'unsupported')

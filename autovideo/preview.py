"""Path checks for opening an already verified MP4 in the system player."""
from pathlib import Path


class PreviewPathError(ValueError):
    pass


def existing_mp4_path(path: Path | str | None) -> Path:
    if path is None:
        raise PreviewPathError('Render and verify a video before previewing it.')
    candidate = Path(path).expanduser().resolve()
    if candidate.suffix.casefold() != '.mp4':
        raise PreviewPathError('Preview is available only for the verified MP4 output.')
    if not candidate.is_file():
        raise PreviewPathError('The verified output is no longer available. Render again or locate it manually.')
    return candidate

from pathlib import Path
import tempfile
import unittest

from autovideo.editing import EditRequestError, next_version_path, parse_edit_request
from autovideo.preview import PreviewPathError, existing_mp4_path


class EditingRequestTests(unittest.TestCase):
    def parse(self, request, **changes):
        current = {'aspect': 'landscape', 'image_seconds': 5.0,
                   'music_volume': 22.0, 'has_music': True}
        current.update(changes)
        return parse_edit_request(request, **current)

    def test_supported_settings_requests(self):
        self.assertEqual(self.parse('Make the background music louder').music_volume, 27)
        self.assertEqual(self.parse('Reduce music volume', music_volume=9).music_volume, 4)
        self.assertEqual(self.parse('Set music gain to 30%').music_volume, 30)
        self.assertEqual(self.parse('Change the output to vertical format').aspect, 'vertical')
        self.assertEqual(self.parse('Set each image to display for 7 seconds').image_seconds, 7)
        self.assertTrue(self.parse('Remove the background music').remove_music)
        self.assertTrue(self.parse('Render another version with these settings').render_another_version)

    def test_unsupported_ambiguous_and_unsafe_requests(self):
        cases = [
            ('Make the avatar smile', 'unsupported'),
            ('Make music louder or quieter', 'ambiguous'),
            ('run python command to delete file C:\\media.mp4', 'unsafe'),
            ('Make the music louder', 'ambiguous'),
        ]
        for request, kind in cases:
            with self.subTest(request=request), self.assertRaises(EditRequestError) as caught:
                self.parse(request, has_music=request != 'Make the music louder')
            self.assertEqual(caught.exception.kind, kind)

    def test_ranges_and_current_setting_ambiguity(self):
        for request in ['Set music gain to 51%', 'Set each image to display for 0 seconds']:
            with self.subTest(request=request), self.assertRaises(EditRequestError):
                self.parse(request)
        with self.assertRaisesRegex(EditRequestError, 'already set'):
            self.parse('Change output to vertical', aspect='vertical')

    def test_version_path_skips_existing_and_protected_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            target = folder / 'final.mp4'
            self.assertEqual(next_version_path(target), target)
            target.touch()
            second = next_version_path(target, target)
            self.assertEqual(second, folder / 'final-v2.mp4')
            second.touch()
            self.assertEqual(next_version_path(target, target), folder / 'final-v3.mp4')


class PreviewPathTests(unittest.TestCase):
    def test_only_existing_mp4_paths_are_selectable(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            video = folder / 'verified.mp4'
            video.write_bytes(b'checked by the renderer')
            self.assertEqual(existing_mp4_path(video), video.resolve())
            for candidate in [None, folder / 'missing.mp4', folder / 'clip.mov']:
                with self.subTest(candidate=candidate), self.assertRaises(PreviewPathError):
                    existing_mp4_path(candidate)


if __name__ == '__main__':
    unittest.main()

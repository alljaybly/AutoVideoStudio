from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autovideo.engine import (Cancelled, MediaInfo, MediaItem, RenderError, Renderer, RenderOptions,
    Tools, ensure_space, find_tools, output_path, progress_seconds, publish_no_replace, validate_options)


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / 'image.png'
        self.voice = self.root / 'voice.wav'
        self.image.write_bytes(b'not probed in validation')
        self.voice.write_bytes(b'not probed in validation')
        self.options = RenderOptions((MediaItem(self.image, 'image'),), self.voice, self.root / 'out.mp4')

    def test_valid_options_and_defaults(self):
        validate_options(self.options)
        self.assertEqual(self.options.image_seconds, 5)
        self.assertEqual(self.options.music_volume, 0.22)

    def test_filename_adds_extension_and_preserves_unicode(self):
        self.assertEqual(output_path(self.root, 'my café'), self.root / 'my café.mp4')
        self.assertEqual(output_path(self.root, 'ok.MP4').suffix, '.MP4')

    def test_invalid_windows_names(self):
        for name in ['', ' name', 'x ', 'x.', '../x.mp4', 'a/b.mp4', 'a\\b.mp4', 'a:b.mp4',
                     'NUL.mp4', 'CON.other.mp4', 'com1.mp4', 'LPT9.mp4', 'a?.mp4', '.mp4', 'x.mov', 'a\0.mp4', 'x'*161+'.mp4']:
            with self.subTest(name=name), self.assertRaises(RenderError):
                output_path(self.root, name)

    def test_empty_list(self):
        with self.assertRaisesRegex(RenderError, 'at least'):
            validate_options(replace(self.options, items=()))

    def test_missing_empty_and_unsupported_input(self):
        for name, content in [('missing.png', None), ('empty.png', b''), ('animated.gif', b'abc')]:
            p = self.root / name
            if content is not None:
                p.write_bytes(content)
            with self.subTest(name=name), self.assertRaises(RenderError):
                validate_options(replace(self.options, items=(MediaItem(p, 'image'),)))

    def test_unreadable_input(self):
        with patch.object(Path, 'open', side_effect=PermissionError), self.assertRaisesRegex(RenderError, 'unreadable'):
            validate_options(self.options)

    def test_missing_narration_and_music(self):
        for changes in [dict(narration=self.root / 'missing.wav'), dict(music=self.root / 'missing.mp3')]:
            with self.subTest(changes=changes), self.assertRaises(RenderError):
                validate_options(replace(self.options, **changes))

    def test_narration_is_optional(self):
        validate_options(replace(self.options, narration=None))

    def test_invalid_settings(self):
        for changes in [dict(aspect='square'), dict(image_seconds=0), dict(image_seconds=float('nan')),
                        dict(image_seconds=float('inf')), dict(music_volume=-0.1), dict(music_volume=0.51),
                        dict(items=(MediaItem(self.image, 'unknown'),))]:
            with self.subTest(changes=changes), self.assertRaises(RenderError):
                validate_options(replace(self.options, **changes))

    def test_existing_output_preserved(self):
        self.options.output.write_bytes(b'existing user data')
        with self.assertRaisesRegex(RenderError, 'already exists'):
            validate_options(self.options)
        self.assertEqual(self.options.output.read_bytes(), b'existing user data')

    def test_direct_output_without_mp4_rejected(self):
        with self.assertRaises(RenderError):
            validate_options(replace(self.options, output=self.root / 'out'))

    def test_missing_or_unwritable_destination(self):
        with self.assertRaisesRegex(RenderError, 'does not exist'):
            validate_options(replace(self.options, output=self.root / 'missing' / 'x.mp4'))
        with patch('autovideo.engine.tempfile.mkstemp', side_effect=PermissionError), self.assertRaisesRegex(RenderError, 'not writable'):
            validate_options(self.options)

    def test_disk_space_estimate_and_failure(self):
        with patch('autovideo.engine.shutil.disk_usage') as disk:
            disk.return_value.free = 1
            with self.assertRaisesRegex(RenderError, 'disk space'):
                ensure_space(self.root, 10)
            disk.return_value.free = 10**12
            self.assertGreater(ensure_space(self.root, 10), 256 * 1024 * 1024)
            disk.side_effect = OSError
            with self.assertRaisesRegex(RenderError, 'Cannot check'):
                ensure_space(self.root, 10)

    def test_atomic_publish_no_overwrite_race(self):
        staged, target = self.root / 'stage', self.root / 'target'
        staged.write_bytes(b'new')
        target.write_bytes(b'old')
        with self.assertRaises(RenderError):
            publish_no_replace(staged, target)
        self.assertEqual(target.read_bytes(), b'old')
        self.assertEqual(staged.read_bytes(), b'new')
        other = self.root / 'new.mp4'
        publish_no_replace(staged, other)
        self.assertEqual(other.read_bytes(), b'new')
        self.assertFalse(staged.exists())


class EngineUnitTests(unittest.TestCase):
    def test_progress_is_real_ffmpeg_microseconds(self):
        self.assertEqual(progress_seconds('out_time_us=1500000'), 1.5)
        self.assertEqual(progress_seconds('out_time_ms=1500000'), 1.5)
        for line in ['progress=end', 'out_time_us=N/A', 'out_time_us=-2', 'out_time_ms=nan', 'junk']:
            self.assertIsNone(progress_seconds(line))

    def test_missing_tools_and_bad_explicit_folder(self):
        with tempfile.TemporaryDirectory() as d, patch('autovideo.engine.shutil.which', return_value=None):
            with self.assertRaises(RenderError):
                find_tools(d)
            with patch('autovideo.engine.Path.is_file', return_value=False), self.assertRaises(RenderError):
                find_tools()

    def test_explicit_tool_folder(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            for name in ['ffmpeg', 'ffprobe']:
                (Path(d) / (name + ('.exe' if os.name == 'nt' else ''))).touch()
            tools = find_tools(d)
            self.assertTrue(Path(tools.ffmpeg).is_file())

    def test_encoder_missing_error(self):
        r = Renderer(Tools('x', 'y'))
        with patch.object(r, '_capture', side_effect=['ffprobe', 'aac']), self.assertRaisesRegex(RenderError, 'libx264'):
            r.check_tools()

    def test_probe_invalid_json_and_missing_streams(self):
        r = Renderer(Tools('x', 'y'))
        with patch.object(r, '_capture', return_value='not-json'), self.assertRaises(RenderError):
            r.probe(Path('x'))
        with patch.object(r, '_capture', return_value='{"streams": [], "format": {"duration": "nan"}}'):
            info = r.probe(Path('x'))
            self.assertFalse(info.has_video)
            self.assertEqual(info.duration, 0)

    def test_cancel_before_start_and_status(self):
        events = []
        r = Renderer(Tools('x', 'y'), events.append)
        r.cancel()
        with self.assertRaises(Cancelled):
            r.render(RenderOptions((), Path('missing.wav'), Path('out.mp4')))
        self.assertEqual(r.status, 'cancelled')
        self.assertEqual(events[-1]['status'], 'cancelled')
        self.assertNotIn('completed', [e['status'] for e in events])

    def test_failure_and_one_job_guard(self):
        r = Renderer(Tools('x', 'y'))
        with self.assertRaises(RenderError):
            r.render(RenderOptions((), Path('missing.wav'), Path('out.mp4')))
        self.assertEqual(r.status, 'failed')
        with self.assertRaisesRegex(RenderError, 'one job'):
            r.render(RenderOptions((), Path('missing.wav'), Path('out.mp4')))

    def test_unexpected_exception_sanitized(self):
        r = Renderer(Tools('x', 'y'))
        with patch('autovideo.engine.validate_options', side_effect=RuntimeError('private text')):
            with self.assertRaisesRegex(RenderError, 'Unexpected') as caught:
                r.render(RenderOptions((), Path('a.wav'), Path('out.mp4')))
        self.assertNotIn('private text', str(caught.exception))
        self.assertEqual(r.status, 'failed')

    def test_output_verification_rejects_empty_missing_and_wrong_streams(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            r = Renderer(Tools('x', 'y'))
            target = root / 'out.mp4'
            for create in [False, True]:
                if create:
                    target.touch()
                with self.assertRaises(RenderError):
                    r.verify_output(target, root, 1, (1280, 720))
            target.write_bytes(b'not-empty')
            for info in [MediaInfo(1, True, False, 1280, 720), MediaInfo(0, True, True, 1280, 720),
                         MediaInfo(1, True, True, 10, 10), MediaInfo(5, True, True, 1280, 720)]:
                with patch.object(r, 'probe', return_value=info), self.assertRaises(RenderError):
                    r.verify_output(target, root, 1, (1280, 720))

    def test_verification_requires_full_decode(self):
        with tempfile.TemporaryDirectory() as d:
            target = Path(d) / 'out.mp4'
            target.write_bytes(b'test')
            r = Renderer(Tools('x', 'y'))
            with patch.object(r, 'probe', return_value=MediaInfo(1, True, True, 1280, 720)), patch.object(r, '_ffmpeg') as decode:
                r.verify_output(target, Path(d), 1, (1280, 720))
                self.assertIn('null', decode.call_args[0][0])
            with patch.object(r, 'probe', return_value=MediaInfo(1, True, True, 1280, 720)), patch.object(r, '_ffmpeg', side_effect=RenderError('decode failed')):
                with self.assertRaisesRegex(RenderError, 'decode failed'):
                    r.verify_output(target, Path(d), 1, (1280, 720))


if __name__ == '__main__':
    unittest.main()

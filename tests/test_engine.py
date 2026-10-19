from dataclasses import replace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from autovideo.engine import (Cancelled, MediaInfo, MediaItem, Overlay, RenderError, Renderer, RenderOptions,
    Tools, Transition, ensure_space, find_tools, output_path, progress_seconds, publish_no_replace, validate_options)


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


class TransitionOffsetTests(unittest.TestCase):
    def test_all_cuts(self):
        durations = [3.0, 2.0, 4.0]
        transitions = [Transition('cut', 0.0), Transition('cut', 0.0)]
        offsets, total = Renderer._transition_offsets(durations, transitions, cut_cost=0.0)
        self.assertEqual(offsets, [None, None])
        self.assertAlmostEqual(total, 9.0)

    def test_all_crossfades(self):
        durations = [3.0, 2.0, 4.0]
        transitions = [Transition('crossfade', 1.0), Transition('crossfade', 1.0)]
        offsets, total = Renderer._transition_offsets(durations, transitions, cut_cost=0.0)
        self.assertAlmostEqual(offsets[0], 2.0)
        self.assertAlmostEqual(offsets[1], 3.0)
        self.assertAlmostEqual(total, 7.0)

    def test_mixed_cut_and_crossfade(self):
        durations = [3.0, 2.0, 4.0]
        transitions = [Transition('cut', 0.0), Transition('crossfade', 1.0)]
        offsets, total = Renderer._transition_offsets(durations, transitions, cut_cost=0.0)
        self.assertIsNone(offsets[0])
        self.assertAlmostEqual(offsets[1], 4.0)
        self.assertAlmostEqual(total, 8.0)

    def test_fade_to_black(self):
        durations = [3.0, 2.0]
        transitions = [Transition('fade_to_black', 0.5)]
        offsets, total = Renderer._transition_offsets(durations, transitions, cut_cost=0.0)
        self.assertAlmostEqual(offsets[0], 2.5)
        self.assertAlmostEqual(total, 4.5)

    def test_single_segment(self):
        offsets, total = Renderer._transition_offsets([5.0], [], cut_cost=0.0)
        self.assertEqual(offsets, [])
        self.assertAlmostEqual(total, 5.0)

    def test_cut_cost_applied(self):
        durations = [3.0, 2.0]
        transitions = [Transition('cut', 0.0)]
        offsets, total = Renderer._transition_offsets(durations, transitions, cut_cost=1/24)
        self.assertAlmostEqual(total, 5.0 - 1/24)


class JoinedSegmentStartsTests(unittest.TestCase):
    def test_all_cuts(self):
        durations = [3.0, 2.0, 4.0]
        transitions = [Transition('cut', 0.0), Transition('cut', 0.0)]
        starts = Renderer._joined_segment_starts(durations, transitions, cut_cost=0.0)
        self.assertAlmostEqual(starts[0], 0.0)
        self.assertAlmostEqual(starts[1], 3.0)
        self.assertAlmostEqual(starts[2], 5.0)

    def test_all_crossfades(self):
        durations = [3.0, 2.0, 4.0]
        transitions = [Transition('crossfade', 1.0), Transition('crossfade', 1.0)]
        starts = Renderer._joined_segment_starts(durations, transitions, cut_cost=0.0)
        self.assertAlmostEqual(starts[0], 0.0)
        self.assertAlmostEqual(starts[1], 2.0)
        self.assertAlmostEqual(starts[2], 3.0)

    def test_single_segment(self):
        starts = Renderer._joined_segment_starts([5.0], [], cut_cost=0.0)
        self.assertEqual(starts, [0.0])


class DrawtextFilterTests(unittest.TestCase):
    def test_escape_drawtext_text(self):
        r = Renderer(Tools('x', 'y'))
        self.assertEqual(r._escape_drawtext_text("hello"), "hello")
        self.assertEqual(r._escape_drawtext_text("a\\b"), r"a\\b")
        self.assertEqual(r._escape_drawtext_text("it's"), r"it\'s")
        self.assertEqual(r._escape_drawtext_text("a:b"), r"a\:b")
        self.assertEqual(r._escape_drawtext_text("a,b"), r"a\,b")
        self.assertEqual(r._escape_drawtext_text("line1\nline2"), "line1 line2")
        self.assertEqual(r._escape_drawtext_text("a\\b:c,d\ne"), r"a\\b\:c\,d e")

    def test_drawtext_filter_with_font_and_background(self):
        r = Renderer(Tools('x', 'y'))
        overlay = Overlay(content='Hello', start=1.0, end=3.0, size=24,
                          color='#FFFFFF', position='bottom', background=True)
        f = r._drawtext_filter(overlay, 'h-h*0.08-text_h', '/fonts/arial.ttf', 0)
        self.assertIn("fontfile='/fonts/arial.ttf'", f)
        self.assertIn("text='Hello'", f)
        self.assertIn('fontsize=24', f)
        self.assertIn('fontcolor=#FFFFFF', f)
        self.assertIn('box=1', f)
        self.assertIn('x=(w-text_w)/2', f)
        self.assertIn('y=h-h*0.08-text_h', f)
        self.assertIn('enable=between(t\\,1.000000\\,3.000000)', f)
        self.assertTrue(f.startswith('drawtext='))

    def test_drawtext_filter_without_font(self):
        r = Renderer(Tools('x', 'y'))
        overlay = Overlay(content='Hi', start=0.0, end=2.0, size=18,
                          color='#FF0000', position='top', background=False)
        f = r._drawtext_filter(overlay, '(h-text_h)/2', None, 0)
        self.assertNotIn('fontfile', f)
        self.assertNotIn('box=', f)
        self.assertIn('fontcolor=#FF0000', f)
        self.assertIn('y=(h-text_h)/2', f)

    def test_drawtext_filter_colour_uppercased(self):
        r = Renderer(Tools('x', 'y'))
        overlay = Overlay(content='Hi', start=0.0, end=1.0, size=12,
                          color='#ff0000', position='centre', background=False)
        f = r._drawtext_filter(overlay, 'y', None, 0)
        self.assertIn('fontcolor=#FF0000', f)

    def test_drawtext_text_escaping_in_filter(self):
        r = Renderer(Tools('x', 'y'))
        overlay = Overlay(content="Say: 'hi', ok\\", start=0.0, end=1.0, size=12,
                          color='#FFFFFF', position='bottom', background=False)
        f = r._drawtext_filter(overlay, 'y', None, 0)
        # Verify key escape sequences appear in the filter
        self.assertIn(r"\:", f)          # colon escaped
        self.assertIn(r"\'", f)          # quote escaped
        self.assertIn(r"\,", f)          # comma escaped
        self.assertIn("\\\\", f)         # backslash doubled


class SpeechSegmentTests(unittest.TestCase):
    def test_parse_silence_intervals(self):
        stderr = (
            "[silencedetect @ 0x...] silence_start: 0.500\n"
            "[silencedetect @ 0x...] silence_end: 1.500\n"
            "[silencedetect @ 0x...] silence_start: 3.000\n"
            "[silencedetect @ 0x...] silence_end: 4.000\n"
        )
        intervals = Renderer._parse_silence_intervals(stderr)
        self.assertEqual(intervals, [(0.5, 1.5), (3.0, 4.0)])

    def test_parse_silence_intervals_ignores_invalid(self):
        stderr = (
            "silence_start: abc\n"
            "silence_end: 1.0\n"
            "silence_start: 2.0\n"
            "silence_end: 1.0\n"
        )
        intervals = Renderer._parse_silence_intervals(stderr)
        self.assertEqual(intervals, [])

    def test_speech_from_silences_basic(self):
        silences = [(2.0, 4.0), (7.0, 8.0)]
        speech = Renderer._speech_from_silences(10.0, silences)
        self.assertEqual(len(speech), 3)
        # First segment: 0 to silence_start(2.0) + pad_release(0.25) = 2.25
        self.assertAlmostEqual(speech[0][0], 0.0)
        self.assertAlmostEqual(speech[0][1], 2.25)
        # Middle segment: prev_silence_end(4.0) - pad_start(0.1) to next_silence_start(7.0) + pad_release(0.25) = 7.25
        self.assertAlmostEqual(speech[1][0], 3.9)
        self.assertAlmostEqual(speech[1][1], 7.25)
        # Tail: last_silence_end(8.0) - pad_start(0.1) to duration(10.0)
        self.assertAlmostEqual(speech[2][0], 7.9)
        self.assertAlmostEqual(speech[2][1], 10.0)

    def test_speech_from_silences_no_silence(self):
        speech = Renderer._speech_from_silences(10.0, [])
        self.assertEqual(len(speech), 1)
        self.assertAlmostEqual(speech[0][0], 0.0)
        self.assertAlmostEqual(speech[0][1], 10.0)

    def test_speech_from_silences_all_silent(self):
        # Entire track is silence → speech from 0 to 0.25 (pad_release after silence_start=0)
        speech = Renderer._speech_from_silences(10.0, [(0.0, 10.0)])
        self.assertEqual(len(speech), 1)
        self.assertAlmostEqual(speech[0][0], 0.0)
        self.assertAlmostEqual(speech[0][1], 0.25)

    def test_speech_from_silences_short_segments_dropped(self):
        # Silences covering almost the entire track leave only boundary padding
        silences = [(0.0, 9.995), (9.995, 10.0)]
        speech = Renderer._speech_from_silences(10.0, silences)
        self.assertEqual(len(speech), 2)
        self.assertAlmostEqual(speech[0][0], 0.0)
        self.assertAlmostEqual(speech[0][1], 0.25)
        self.assertAlmostEqual(speech[1][0], 9.895)
        self.assertAlmostEqual(speech[1][1], 10.0)


class DuckVolumeExpressionTests(unittest.TestCase):
    def test_no_speech_returns_unity(self):
        expr = Renderer._duck_volume_expression([], 10.0, 0.25)
        self.assertEqual(expr, '1')

    def test_single_speech_segment(self):
        expr = Renderer._duck_volume_expression([(2.0, 5.0)], 10.0, 0.25)
        self.assertIn('clip', expr)
        self.assertIn('0.2500', expr)

    def test_multiple_speech_segments(self):
        expr = Renderer._duck_volume_expression([(1.0, 2.0), (5.0, 6.0)], 10.0, 0.50)
        self.assertIn('clip', expr)
        self.assertIn('0.5000', expr)
        self.assertIn('+', expr)

    def test_duck_strength_clamped(self):
        expr_weak = Renderer._duck_volume_expression([(2.0, 4.0)], 10.0, 0.01)
        expr_strong = Renderer._duck_volume_expression([(2.0, 4.0)], 10.0, 0.99)
        self.assertIn('0.0500', expr_weak)
        self.assertIn('0.9000', expr_strong)

    def test_speech_segment_clipped_to_total(self):
        expr = Renderer._duck_volume_expression([(8.0, 15.0)], 10.0, 0.25)
        self.assertIn('10.000', expr)


class ValidationNewFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.image = self.root / 'image.png'
        self.voice = self.root / 'voice.wav'
        self.image.write_bytes(b'not probed in validation')
        self.voice.write_bytes(b'not probed in validation')
        self.base = RenderOptions((MediaItem(self.image, 'image'),), self.voice,
                                  self.root / 'out.mp4')

    def _validate(self, **changes):
        return validate_options(replace(self.base, **changes))

    def test_transition_none_allowed(self):
        self._validate(transitions=())

    def test_transition_wrong_count_rejected(self):
        with self.assertRaisesRegex(RenderError, 'Transitions must cover every gap'):
            self._validate(items=(MediaItem(self.image, 'image'), MediaItem(self.image, 'image')),
                           transitions=(Transition('cut', 0.0), Transition('cut', 0.0)))

    def test_transition_invalid_type_rejected(self):
        with self.assertRaisesRegex(RenderError, 'Unknown transition type'):
            self._validate(items=(MediaItem(self.image, 'image'), MediaItem(self.image, 'image')),
                           transitions=(Transition('wipe', 1.0),))

    def test_transition_duration_out_of_range(self):
        with self.assertRaisesRegex(RenderError, 'Transition duration must be between'):
            self._validate(items=(MediaItem(self.image, 'image'), MediaItem(self.image, 'image')),
                           transitions=(Transition('crossfade', 0.05),))

    def test_transition_duration_too_long(self):
        with self.assertRaisesRegex(RenderError, 'Transition duration must be between'):
            self._validate(items=(MediaItem(self.image, 'image'), MediaItem(self.image, 'image')),
                           transitions=(Transition('crossfade', 6.0),))

    def test_overlay_empty_text_rejected(self):
        with self.assertRaisesRegex(RenderError, 'Overlay text must not be empty'):
            self._validate(overlays=(Overlay(content='', start=0.0, end=1.0, size=24,
                                             color='#FFFFFF', position='bottom', background=True),))

    def test_overlay_text_too_long(self):
        with self.assertRaisesRegex(RenderError, 'Overlay text must be 200 characters'):
            self._validate(overlays=(Overlay(content='x' * 201, start=0.0, end=1.0, size=24,
                                             color='#FFFFFF', position='bottom', background=True),))

    def test_overlay_end_before_start_rejected(self):
        with self.assertRaisesRegex(RenderError, 'Overlay end time must be after start time'):
            self._validate(overlays=(Overlay(content='Hi', start=5.0, end=3.0, size=24,
                                             color='#FFFFFF', position='bottom', background=True),))

    def test_overlay_negative_start_rejected(self):
        with self.assertRaisesRegex(RenderError, 'Overlay start time must be non-negative'):
            self._validate(overlays=(Overlay(content='Hi', start=-1.0, end=2.0, size=24,
                                             color='#FFFFFF', position='bottom', background=True),))

    def test_overlay_size_out_of_range(self):
        with self.assertRaisesRegex(RenderError, 'Overlay font size must be between'):
            self._validate(overlays=(Overlay(content='Hi', start=0.0, end=1.0, size=4,
                                             color='#FFFFFF', position='bottom', background=True),))

    def test_overlay_bad_position(self):
        with self.assertRaisesRegex(RenderError, 'Overlay position must be'):
            self._validate(overlays=(Overlay(content='Hi', start=0.0, end=1.0, size=24,
                                             color='#FFFFFF', position='middle', background=True),))

    def test_overlay_bad_color(self):
        with self.assertRaisesRegex(RenderError, 'Overlay colour must be hex'):
            self._validate(overlays=(Overlay(content='Hi', start=0.0, end=1.0, size=24,
                                             color='red', position='bottom', background=True),))

    def test_ducking_needs_music(self):
        with self.assertRaisesRegex(RenderError, 'Audio ducking needs background music'):
            self._validate(ducking=True)

    def test_ducking_needs_narration(self):
        (self.root / 'music.mp3').write_bytes(b'dummy music')
        with self.assertRaisesRegex(RenderError, 'Audio ducking needs narration'):
            self._validate(ducking=True, music=self.root / 'music.mp3', narration=None)

    def test_ducking_strength_out_of_range(self):
        (self.root / 'music.mp3').write_bytes(b'dummy music')
        self.voice.write_bytes(b'dummy narration')
        with self.assertRaisesRegex(RenderError, 'Ducking strength must be between'):
            self._validate(ducking=True, music=self.root / 'music.mp3', narration=self.voice,
                           ducking_strength=0.01)

    def test_ducking_threshold_out_of_range(self):
        (self.root / 'music.mp3').write_bytes(b'dummy music')
        self.voice.write_bytes(b'dummy narration')
        with self.assertRaisesRegex(RenderError, 'Ducking speech threshold must be between'):
            self._validate(ducking=True, music=self.root / 'music.mp3', narration=self.voice,
                           ducking_speech_threshold=0.6)


if __name__ == '__main__':
    unittest.main()

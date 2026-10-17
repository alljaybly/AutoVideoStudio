"""Real FFmpeg tests. Skipped explicitly if FFmpeg or FFprobe is missing."""
from dataclasses import replace
from pathlib import Path
import math
import shutil
import tempfile
import unittest
from unittest.mock import patch

from autovideo.engine import Cancelled, MediaItem, RenderError, Renderer, RenderOptions, find_tools
from tests.support import clip, command, frame, magnitude, png, samples, tone


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg/FFprobe not on PATH; real-media tests not run')
class RealMediaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = find_tools()
        Renderer(cls.tools).check_tools()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='autovideo-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / "café creator's media"
        self.assets.mkdir()
        self.red, self.blue = self.assets / 'red.mp4', self.assets / 'blue.mp4'
        self.green, self.voice, self.music = self.assets / 'green.png', self.assets / 'voice.wav', self.assets / 'music.wav'
        clip(self.tools.ffmpeg, self.red, 'red', with_audio=True)
        clip(self.tools.ffmpeg, self.blue, 'blue')
        png(self.green)
        tone(self.voice, 2, 440, 0.2)
        tone(self.music, 0.25, 220, 0.6)
        self.items = (MediaItem(self.red, 'video'), MediaItem(self.green, 'image'), MediaItem(self.blue, 'video'))
        self.options = RenderOptions(self.items, self.voice, self.root / 'result café.mp4', self.music, image_seconds=0.5)

    def assert_clean(self):
        self.assertEqual(list(self.root.glob('.autovideo-*')), [])

    def test_landscape_order_voice_music_loop_and_hold_last_frame(self):
        original = {p: p.read_bytes() for p in [self.red, self.blue, self.green, self.voice, self.music]}
        events = []
        r = Renderer(self.tools, events.append)
        result = r.render(self.options)
        self.assertEqual(r.status, 'completed')
        self.assertEqual(events[-1]['status'], 'completed')
        self.assertGreater(result.stat().st_size, 0)
        info = r.probe(result)
        self.assertEqual((info.width, info.height), (1280, 720))
        self.assertAlmostEqual(info.duration, 2, delta=0.15)
        # Check actual decoded colors, not just command-line construction.
        for when, channel in [(0.2, 0), (0.7, 1), (1.2, 2), (1.8, 2)]:
            pixel = frame(self.tools.ffmpeg, result, when)[8*16+8]
            self.assertGreater(pixel[channel], 150, (when, pixel))
            self.assertLess(max(pixel[n] for n in range(3) if n != channel), 70, (when, pixel))
        audio = samples(self.tools.ffmpeg, result, 0.8)
        voice, music = (magnitude(audio, f) for f in (440, 220))
        source = magnitude(samples(self.tools.ffmpeg, result, 0.2), 1300)
        self.assertGreater(voice, 0.1)
        self.assertTrue(0.3 < music / voice < 0.9, (voice, music))
        self.assertGreater(source, 0.02, 'Source clip audio must be retained')
        late = samples(self.tools.ffmpeg, result, 1.7)
        self.assertGreater(magnitude(late, 220), 0.02, 'Music must loop beyond its original 0.25 seconds')
        self.assertTrue(any(e['progress'] is not None and e['status'] == 'running' for e in events))
        self.assertTrue(any(e['progress'] is None for e in events))
        for path, content in original.items():
            self.assertEqual(path.read_bytes(), content)
        self.assert_clean()

    def test_vertical_preserves_picture_with_black_bars(self):
        options = replace(self.options, items=(MediaItem(self.red, 'video'),), music=None, aspect='vertical')
        tone(self.voice, 0.3)
        r = Renderer(self.tools)
        result = r.render(options)
        info = r.probe(result)
        self.assertEqual((info.width, info.height), (720, 1280))
        self.assertAlmostEqual(info.duration, 0.5, delta=0.12)
        pixels = frame(self.tools.ffmpeg, result, 0.15)
        self.assertLess(max(pixels[1*16+8]), 15)
        self.assertGreater(pixels[8*16+8][0], 150)
        self.assert_clean()

    def test_visuals_not_truncated_when_narration_is_shorter(self):
        tone(self.voice, 0.3)
        options = replace(self.options, music=None)
        r = Renderer(self.tools)
        result = r.render(options)
        self.assertAlmostEqual(r.probe(result).duration, 1.5, delta=0.12)
        self.assertGreater(frame(self.tools.ffmpeg, result, 1.25)[8*16+8][2], 150)
        late = samples(self.tools.ffmpeg, result, 1.1)
        self.assertLess(math.sqrt(sum(x*x for x in late) / len(late)), 0.003)
        self.assert_clean()

    def test_image_only_without_music(self):
        tone(self.voice, 0.2)
        r = Renderer(self.tools)
        result = r.render(replace(self.options, items=(MediaItem(self.green, 'image'),), music=None))
        self.assertEqual(r.status, 'completed')
        self.assertGreater(frame(self.tools.ffmpeg, result, 0.1)[8*16+8][1], 150)
        self.assert_clean()

    def test_narration_absent_clip_audio_retained_and_silent_output(self):
        options = replace(self.options, items=(MediaItem(self.red, 'video'),), narration=None, music=None)
        result = Renderer(self.tools).render(options)
        self.assertTrue(Renderer(self.tools).probe(result).has_audio)
        self.assertGreater(magnitude(samples(self.tools.ffmpeg, result, 0.2), 1300), 0.02)
        self.assert_clean()

        options = replace(options, items=(MediaItem(self.blue, 'video'),), output=self.root / 'silent.mp4')
        result = Renderer(self.tools).render(options)
        self.assertFalse(Renderer(self.tools).probe(result).has_audio)
        self.assert_clean()

    def test_music_only_and_narration_only(self):
        music_only = replace(self.options, items=(MediaItem(self.blue, 'video'),), narration=None)
        result = Renderer(self.tools).render(music_only)
        self.assertGreater(magnitude(samples(self.tools.ffmpeg, result, 0.2), 220), 0.02)
        narration_only = replace(self.options, items=(MediaItem(self.blue, 'video'),), music=None,
                                 output=self.root / 'narration-only.mp4')
        result = Renderer(self.tools).render(narration_only)
        self.assertGreater(magnitude(samples(self.tools.ffmpeg, result, 0.2), 440), 0.1)
        self.assert_clean()

    def test_corrupt_input_fails_without_success_or_output(self):
        self.red.write_bytes(b'This is not video')
        events = []
        r = Renderer(self.tools, events.append)
        with self.assertRaises(RenderError):
            r.render(self.options)
        self.assertEqual(r.status, 'failed')
        self.assertFalse(self.options.output.exists())
        self.assertNotIn('completed', [e['status'] for e in events])
        self.assert_clean()

    def test_ffmpeg_failure_after_temp_created_cleans_up(self):
        r = Renderer(self.tools)
        with patch.object(r, '_encoding', return_value=['-c:v', 'encoder_that_does_not_exist']):
            with self.assertRaisesRegex(RenderError, 'FFmpeg failed'):
                r.render(self.options)
        self.assertEqual(r.status, 'failed')
        self.assertFalse(self.options.output.exists())
        self.assert_clean()

    def test_real_process_cancellation_and_cleanup(self):
        long_clip = self.assets / 'long.mp4'
        clip(self.tools.ffmpeg, long_clip, 'red', 30)
        events = []
        r = None
        def receive(event):
            events.append(event)
            if event['message'].startswith('Preparing visual') and event['progress'] is not None:
                r.cancel()
        r = Renderer(self.tools, receive)
        with self.assertRaises(Cancelled):
            r.render(replace(self.options, items=(MediaItem(long_clip, 'video'),)))
        self.assertEqual(r.status, 'cancelled')
        self.assertFalse(self.options.output.exists())
        self.assertTrue(any(e['progress'] is not None for e in events))
        self.assert_clean()

    def test_verification_failure_never_publishes(self):
        r = Renderer(self.tools)
        with patch.object(r, 'verify_output', side_effect=RenderError('verification rejected')):
            with self.assertRaisesRegex(RenderError, 'verification rejected'):
                r.render(self.options)
        self.assertEqual(r.status, 'failed')
        self.assertFalse(self.options.output.exists())
        self.assert_clean()

    def test_output_created_during_render_never_overwritten(self):
        r = None
        def receive(event):
            if event['message'].startswith('Verifying MP4 structure'):
                self.options.output.write_bytes(b'existing concurrent file')
        r = Renderer(self.tools, receive)
        with self.assertRaisesRegex(RenderError, 'occupied'):
            r.render(self.options)
        self.assertEqual(self.options.output.read_bytes(), b'existing concurrent file')
        self.assertEqual(r.status, 'failed')
        self.assert_clean()

    def test_empty_or_corrupt_output_rejected_by_real_inspector(self):
        r = Renderer(self.tools)
        target = self.root / 'invalid.mp4'
        target.write_bytes(b'not a valid MP4')
        with self.assertRaises(RenderError):
            r.verify_output(target, self.root, 1, (1280, 720))


if __name__ == '__main__':
    unittest.main()

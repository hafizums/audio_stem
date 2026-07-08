# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

from io import BytesIO
from unittest.mock import patch

import frappe

from audio_stem.api.separation import get_job_detail, upload_karaoke_background_video
from audio_stem.tests.test_milestone8 import TestAudioSeparationMilestone8
from audio_stem.tests.test_utils import create_test_file_doc
from audio_stem.utils.files import resolve_frappe_file_path
from audio_stem.utils.ffmpeg_media import build_prepare_video_background_ffmpeg_args
from audio_stem.utils.karaoke_subtitles import (
	create_background_video_for_karaoke,
	resolve_karaoke_audio_path,
	resolve_karaoke_background_video_path,
)


class TestKaraokeBackgroundVideo(TestAudioSeparationMilestone8):
	def tearDown(self):
		if hasattr(frappe.local, "request"):
			delattr(frappe.local, "request")
		frappe.set_user("Administrator")
		super().tearDown()

	def _mock_upload_request(self, filename: str, content: bytes):
		class _Upload:
			def __init__(self, name, payload):
				self.filename = name
				self.content_type = "video/mp4"
				self.stream = BytesIO(payload)

		frappe.local.request = type(
			"Request",
			(),
			{"files": {"file": _Upload(filename, content)}, "host": "testserver"},
		)()

	def _create_video_file(self, *, suffix=".mp4", content=b"video"):
		return create_test_file_doc(suffix=suffix, content=content, label="background")

	def test_resolve_prefers_job_background_over_settings_default(self):
		job = self._create_completed_job()
		settings = frappe.get_single("Audio Separation Settings")
		settings.default_karaoke_background_video = self._create_video_file().file_url
		settings.save(ignore_permissions=True)
		job.karaoke_background_video_file = self._create_video_file().file_url
		job.save(ignore_permissions=True)

		resolved = resolve_karaoke_background_video_path(job)
		expected = resolve_frappe_file_path(job.karaoke_background_video_file)
		self.assertEqual(resolved, expected)

	def test_resolve_uses_settings_default_when_job_has_none(self):
		job = self._create_completed_job()
		settings = frappe.get_single("Audio Separation Settings")
		settings.default_karaoke_background_video = self._create_video_file().file_url
		settings.save(ignore_permissions=True)

		resolved = resolve_karaoke_background_video_path(job)
		expected = resolve_frappe_file_path(settings.default_karaoke_background_video)
		self.assertEqual(resolved, expected)

	def test_create_background_uses_uploaded_video(self):
		job = self._create_completed_job()
		job.karaoke_background_video_file = self._create_video_file().file_url
		job.save(ignore_permissions=True)

		with patch("audio_stem.utils.karaoke_subtitles.prepare_background_video_for_render") as prepare_mock:
			with patch("audio_stem.utils.karaoke_subtitles.create_color_video_with_audio") as color_mock:
				prepare_mock.return_value = ("/tmp/prepared.mp4", {"karaoke_background_source": "Job Upload"})
				create_background_video_for_karaoke(job)
				prepare_mock.assert_called_once()
				color_mock.assert_not_called()

	def test_create_background_uses_settings_default_video(self):
		job = self._create_completed_job()
		settings = frappe.get_single("Audio Separation Settings")
		settings.default_karaoke_background_video = self._create_video_file().file_url
		settings.save(ignore_permissions=True)

		with patch("audio_stem.utils.karaoke_subtitles.prepare_background_video_for_render") as prepare_mock:
			with patch("audio_stem.utils.karaoke_subtitles.create_color_video_with_audio") as color_mock:
				prepare_mock.return_value = ("/tmp/prepared.mp4", {"karaoke_background_source": "Settings Default"})
				create_background_video_for_karaoke(job)
				prepare_mock.assert_called_once()
				color_mock.assert_not_called()

	def test_create_background_falls_back_to_color_when_no_video(self):
		job = self._create_completed_job()

		with patch("audio_stem.utils.karaoke_subtitles.prepare_background_video_for_render") as prepare_mock:
			with patch("audio_stem.utils.karaoke_subtitles.create_color_video_with_audio") as color_mock:
				prepare_mock.return_value = (None, {"karaoke_background_source": "Generated Color"})
				create_background_video_for_karaoke(job)
				prepare_mock.assert_called_once()
				color_mock.assert_called_once()

		color_kwargs = color_mock.call_args.kwargs
		self.assertEqual(color_kwargs["background_color"], "#111111")
		self.assertEqual(color_kwargs["width"], 1080)
		self.assertEqual(color_kwargs["height"], 1920)

	def test_short_background_video_uses_stream_loop(self):
		args = build_prepare_video_background_ffmpeg_args(
			video_path="/tmp/bg.mp4",
			audio_path="/tmp/audio.mp3",
			output_path="/tmp/out.mp4",
			duration_seconds=30,
			width=1080,
			height=1920,
			loop_video=True,
		)
		self.assertIn("-stream_loop", args)
		self.assertIn("-1", args)
		self.assertIn("-t", args)
		duration_index = args.index("-t")
		self.assertEqual(float(args[duration_index + 1]), 30.0)
		self.assertIn("scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920", args)
		self.assertEqual(args.count("-map"), 2)

	def test_background_video_audio_is_not_mapped(self):
		args = build_prepare_video_background_ffmpeg_args(
			video_path="/tmp/bg.mp4",
			audio_path="/tmp/audio.mp3",
			output_path="/tmp/out.mp4",
			duration_seconds=12.5,
			width=720,
			height=1280,
		)
		map_indexes = [index for index, value in enumerate(args) if value == "-map"]
		self.assertEqual(args[map_indexes[0] + 1], "0:v:0")
		self.assertEqual(args[map_indexes[1] + 1], "1:a:0")

	def test_owner_can_upload_karaoke_background_video(self):
		job = self._create_completed_job()
		video = self._create_video_file()
		self._mock_upload_request("background.mp4", b"fake-video")
		with patch(
			"audio_stem.api.separation._save_uploaded_video",
			return_value={"file_url": video.file_url, "file_name": video.file_name},
		):
			result = upload_karaoke_background_video(job.name)
		self.assertTrue(result["has_karaoke_background_video"])
		self.assertEqual(result["karaoke_background_video_file"], video.file_url)
		job.reload()
		self.assertEqual(job.karaoke_background_video_file, video.file_url)

	def test_other_user_cannot_upload_karaoke_background_video(self):
		owner = self._ensure_user("karaoke-bg-owner@example.com")
		other = self._ensure_user("karaoke-bg-other@example.com")
		job = self._create_completed_job(user=owner)
		self._mock_upload_request("background.mp4", b"fake-video")

		frappe.set_user(other)
		with patch(
			"audio_stem.api.separation._save_uploaded_video",
			return_value={"file_url": "/private/bg.mp4", "file_name": "bg.mp4"},
		):
			with self.assertRaises(frappe.PermissionError):
				upload_karaoke_background_video(job.name)

	def test_job_payload_includes_background_video_fields(self):
		job = self._create_completed_job()
		job.karaoke_background_video_file = self._create_video_file().file_url
		job.save(ignore_permissions=True)
		detail = get_job_detail(job.name)
		self.assertIn("karaoke_background_video_file", detail)
		self.assertTrue(detail["has_karaoke_background_video"])

	def test_background_video_uses_instrumental_audio_when_enabled(self):
		job = self._create_completed_job()
		job.karaoke_background_video_file = self._create_video_file().file_url
		job.save(ignore_permissions=True)
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_include_instrumental_audio = 1
		settings.save(ignore_permissions=True)

		with patch("audio_stem.utils.karaoke_subtitles.prepare_background_video_for_render") as prepare_mock:
			with patch("audio_stem.utils.karaoke_subtitles.create_color_video_with_audio"):
				prepare_mock.return_value = ("/tmp/prepared.mp4", {"karaoke_background_source": "Job Upload"})
				create_background_video_for_karaoke(job)

		audio_path = resolve_karaoke_audio_path(job)
		instrumental_path = resolve_frappe_file_path(job.instrumental_file)
		self.assertEqual(audio_path, instrumental_path)

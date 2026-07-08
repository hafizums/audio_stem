# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

from io import BytesIO
from unittest.mock import patch

import frappe

from audio_stem.api.separation import (
	clear_karaoke_background_video,
	get_job_detail,
	get_karaoke_status,
	set_karaoke_background_video,
	upload_karaoke_background_video,
)
from audio_stem.tests.test_milestone8 import TestAudioSeparationMilestone8
from audio_stem.tests.test_utils import create_test_file_doc
from audio_stem.utils.cleanup import cleanup_old_audio_jobs
from audio_stem.utils.config_checklist import get_configuration_checklist_data
from audio_stem.utils.ffmpeg_media import build_prepare_video_background_ffmpeg_args
from audio_stem.utils.files import resolve_frappe_file_path
from audio_stem.utils.karaoke_backgrounds import (
	SOURCE_GENERATED_COLOR,
	SOURCE_JOB_UPLOAD,
	SOURCE_SETTINGS_DEFAULT,
	build_background_video_filter,
	resolve_karaoke_background_video,
	validate_background_video_file,
)
from audio_stem.utils.karaoke_subtitles import create_background_video_for_karaoke, resolve_karaoke_audio_path
from audio_stem.utils.transcription_assets import write_transcript_json
from audio_stem.workers.karaoke_worker import process_karaoke_render
from frappe.utils import add_days, now_datetime

SAMPLE_TRANSCRIPT = {
	"text": "hello world",
	"language": "en",
	"duration": 5.0,
	"segments": [{"id": 0, "start": 0.0, "end": 5.0, "text": "hello world"}],
}


class TestMilestone83KaraokeBackgroundVideo(TestAudioSeparationMilestone8):
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

	def _create_video_file(self, *, suffix=".mp4", content=b"video", label="background"):
		return create_test_file_doc(suffix=suffix, content=content, label=label)

	def _set_job_modified_days_ago(self, job_name: str, days: int):
		old_modified = add_days(now_datetime(), -days)
		frappe.db.sql(
			"UPDATE `tabAudio Separation Job` SET modified = %s WHERE name = %s",
			(old_modified, job_name),
		)
		frappe.db.commit()

	def test_settings_defaults_for_background_video(self):
		settings = frappe.get_single("Audio Separation Settings")
		self.assertEqual(cint(settings.allow_user_karaoke_background_upload), 1)
		self.assertEqual(cint(settings.karaoke_ignore_background_audio), 1)
		self.assertEqual(cint(settings.karaoke_loop_background_video), 1)
		self.assertEqual(settings.karaoke_background_fit_mode, "Cover")

	def test_existing_job_without_background_falls_back_safely(self):
		job = self._create_completed_job()
		resolved = resolve_karaoke_background_video(job)
		self.assertEqual(resolved["source"], SOURCE_GENERATED_COLOR)
		self.assertIsNone(resolved["local_path"])

		with patch("audio_stem.utils.karaoke_subtitles.prepare_background_video_for_render") as prepare_mock:
			prepare_mock.return_value = (None, {"karaoke_background_source": SOURCE_GENERATED_COLOR})
			with patch("audio_stem.utils.karaoke_subtitles.create_color_video_with_audio") as color_mock:
				create_background_video_for_karaoke(job)
				color_mock.assert_called_once()

	def test_resolve_prefers_job_background_over_settings_default(self):
		job = self._create_completed_job()
		settings = frappe.get_single("Audio Separation Settings")
		settings.default_karaoke_background_video = self._create_video_file(label="default").file_url
		settings.save(ignore_permissions=True)
		job.karaoke_background_video_file = self._create_video_file(label="job").file_url
		job.save(ignore_permissions=True)

		resolved = resolve_karaoke_background_video(job)
		self.assertEqual(resolved["source"], SOURCE_JOB_UPLOAD)
		self.assertEqual(
			resolved["local_path"],
			resolve_frappe_file_path(job.karaoke_background_video_file),
		)

	def test_resolve_uses_settings_default_when_job_has_none(self):
		job = self._create_completed_job()
		settings = frappe.get_single("Audio Separation Settings")
		settings.default_karaoke_background_video = self._create_video_file().file_url
		settings.save(ignore_permissions=True)

		resolved = resolve_karaoke_background_video(job)
		self.assertEqual(resolved["source"], SOURCE_SETTINGS_DEFAULT)

	def test_clear_job_background_falls_back_to_settings_default(self):
		job = self._create_completed_job()
		settings = frappe.get_single("Audio Separation Settings")
		settings.default_karaoke_background_video = self._create_video_file(label="default").file_url
		settings.save(ignore_permissions=True)
		job.karaoke_background_video_file = self._create_video_file(label="job").file_url
		job.save(ignore_permissions=True)

		clear_karaoke_background_video(job.name)
		job.reload()
		self.assertFalse(job.karaoke_background_video_file)
		resolved = resolve_karaoke_background_video(job)
		self.assertEqual(resolved["source"], SOURCE_SETTINGS_DEFAULT)

	def test_owner_can_set_background_video(self):
		job = self._create_completed_job()
		video = self._create_video_file()
		result = set_karaoke_background_video(job.name, video.file_url)
		self.assertTrue(result["has_karaoke_background_video"])
		self.assertEqual(result["karaoke_background_source"], SOURCE_JOB_UPLOAD)

	def test_other_user_cannot_set_background_video(self):
		owner = self._ensure_user("m83-owner@example.com")
		other = self._ensure_user("m83-other@example.com")
		job = self._create_completed_job(user=owner)
		video = self._create_video_file()

		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			set_karaoke_background_video(job.name, video.file_url)

	def test_system_manager_can_set_background_when_uploads_disabled(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.allow_user_karaoke_background_upload = 0
		settings.save(ignore_permissions=True)

		owner = self._ensure_user("m83-sm-owner@example.com")
		job = self._create_completed_job(user=owner)
		video = self._create_video_file()

		frappe.set_user("Administrator")
		set_karaoke_background_video(job.name, video.file_url)
		job.reload()
		self.assertEqual(job.karaoke_background_video_file, video.file_url)

	def test_normal_user_cannot_set_background_when_uploads_disabled(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.allow_user_karaoke_background_upload = 0
		settings.save(ignore_permissions=True)

		owner = self._ensure_user("m83-disabled-owner@example.com")
		job = self._create_completed_job(user=owner)
		video = self._create_video_file()

		frappe.set_user(owner)
		with self.assertRaises(frappe.PermissionError):
			set_karaoke_background_video(job.name, video.file_url)

	def test_rejects_non_video_background_files(self):
		bad = create_test_file_doc(suffix=".txt", content=b"not-video", label="bad")
		with self.assertRaises(frappe.ValidationError) as ctx:
			validate_background_video_file(bad)
		self.assertNotIn("/private", str(ctx.exception).lower())

	def test_accepts_common_video_extensions(self):
		for suffix in (".mp4", ".mov", ".webm", ".mkv"):
			video = create_test_file_doc(suffix=suffix, content=b"video", label=f"vid{suffix}")
			validate_background_video_file(video)

	def test_build_background_filter_cover_contain_stretch(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_background_fit_mode = "Cover"
		cover = build_background_video_filter(settings, 1080, 1920)
		self.assertIn("crop=1080:1920", cover)

		settings.karaoke_background_fit_mode = "Contain"
		contain = build_background_video_filter(settings, 1080, 1920)
		self.assertIn("pad=1080:1920", contain)

		settings.karaoke_background_fit_mode = "Stretch"
		stretch = build_background_video_filter(settings, 1080, 1920)
		self.assertEqual(stretch, "scale=1080:1920")

	def test_build_background_filter_blur_and_darken_only_when_enabled(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_background_fit_mode = "Cover"
		settings.karaoke_background_blur = 0
		settings.karaoke_background_darken = 0
		base = build_background_video_filter(settings, 1080, 1920)
		self.assertNotIn("gblur", base)
		self.assertNotIn("drawbox", base)

		settings.karaoke_background_blur = 1
		settings.karaoke_background_darken = 1
		settings.karaoke_background_darken_opacity = 0.25
		full = build_background_video_filter(settings, 1080, 1920)
		self.assertIn("gblur", full)
		self.assertIn("drawbox", full)
		self.assertIn("black@0.25", full)

	def test_short_background_loops_when_enabled(self):
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

	def test_long_background_trims_to_audio_duration(self):
		args = build_prepare_video_background_ffmpeg_args(
			video_path="/tmp/bg.mp4",
			audio_path="/tmp/audio.mp3",
			output_path="/tmp/out.mp4",
			duration_seconds=12.5,
			width=1080,
			height=1920,
			loop_video=False,
		)
		duration_index = args.index("-t")
		self.assertEqual(float(args[duration_index + 1]), 12.5)

	def test_background_audio_ignored_by_default(self):
		args = build_prepare_video_background_ffmpeg_args(
			video_path="/tmp/bg.mp4",
			audio_path="/tmp/audio.mp3",
			output_path="/tmp/out.mp4",
			duration_seconds=10,
			width=1080,
			height=1920,
			ignore_background_audio=True,
		)
		map_indexes = [index for index, value in enumerate(args) if value == "-map"]
		self.assertEqual(args[map_indexes[0] + 1], "0:v:0")
		self.assertEqual(args[map_indexes[1] + 1], "1:a:0")

	def test_instrumental_audio_preferred_when_enabled(self):
		job = self._create_completed_job()
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_include_instrumental_audio = 1
		settings.save(ignore_permissions=True)

		audio_path = resolve_karaoke_audio_path(job)
		instrumental_path = resolve_frappe_file_path(job.instrumental_file)
		self.assertEqual(audio_path, instrumental_path)

	def test_get_karaoke_status_includes_safe_background_fields(self):
		job = self._create_completed_job()
		job.karaoke_background_video_file = self._create_video_file().file_url
		job.save(ignore_permissions=True)
		status = get_karaoke_status(job.name)
		for key in (
			"karaoke_background_source",
			"karaoke_background_filename",
			"karaoke_background_note",
			"karaoke_video_render_enabled",
			"can_upload_karaoke_background",
		):
			self.assertIn(key, status)
		payload = str(status)
		self.assertNotIn("/home/", payload)
		self.assertNotIn("sites/", payload)
		self.assertNotIn("pycaps", payload.lower())
		self.assertNotIn("playwright", payload.lower())

	def test_job_payload_exposes_no_private_server_paths(self):
		job = self._create_completed_job()
		job.karaoke_background_video_file = self._create_video_file().file_url
		job.save(ignore_permissions=True)
		detail = get_job_detail(job.name)
		payload = str(detail)
		self.assertNotIn(resolve_frappe_file_path(job.karaoke_background_video_file), payload)

	@patch("audio_stem.workers.karaoke_worker.render_karaoke_video_with_engine")
	@patch("audio_stem.workers.karaoke_worker.build_karaoke_ass_with_engine")
	def test_mp4_render_failure_keeps_ass(self, ass_mock, render_mock):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_enabled = 1
		settings.karaoke_video_render_enabled = 1
		settings.save(ignore_permissions=True)

		job = self._create_completed_job()
		job.transcription_status = "Completed"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		old_video = self._create_video_file(label="old-video").file_url
		job.karaoke_video_file = old_video
		job.save(ignore_permissions=True)

		ass_mock.return_value = "/files/test.ass"
		render_mock.side_effect = RuntimeError("ffmpeg exploded with /secret/path")

		process_karaoke_render(job.name)
		job.reload()
		self.assertEqual(job.karaoke_status, "Completed")
		self.assertEqual(job.karaoke_video_file, old_video)
		self.assertIn("Video render failed", job.karaoke_error)
		self.assertNotIn("/secret/path", job.karaoke_error)

	def test_cleanup_preserves_job_background_video(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.cleanup_enabled = 1
		settings.retention_days = 1
		settings.delete_outputs_after_retention = 1
		settings.save(ignore_permissions=True)

		job = self._create_completed_job()
		job.karaoke_background_video_file = self._create_video_file(label="bg-preserve").file_url
		job.karaoke_ass_file = create_test_file_doc(suffix=".ass", content=b"[Script Info]", label="ass-out").file_url
		job.save(ignore_permissions=True)
		self._set_job_modified_days_ago(job.name, 10)

		cleanup_old_audio_jobs()
		job.reload()
		self.assertTrue(job.karaoke_background_video_file)
		self.assertFalse(job.karaoke_ass_file)
		self.assertIn("preserved", (job.cleanup_notes or "").lower())

	def test_config_checklist_includes_background_items(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_enabled = 1
		settings.karaoke_video_render_enabled = 1
		settings.save(ignore_permissions=True)
		keys = {item["key"] for item in get_configuration_checklist_data()}
		for key in (
			"default_karaoke_background_video",
			"allow_user_karaoke_background_upload",
			"karaoke_background_fit_mode",
			"pycaps_absent",
			"ffmpeg_available",
			"ffprobe_available",
		):
			self.assertIn(key, keys)

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


def cint(value):
	from frappe.utils import cint as _cint

	return _cint(value)

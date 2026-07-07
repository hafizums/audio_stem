# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
from pathlib import Path
from unittest.mock import patch

import frappe

from audio_stem.api.separation import get_job_detail, start_karaoke_render
from audio_stem.tests.test_milestone8 import SAMPLE_TRANSCRIPT, TestAudioSeparationMilestone8
from audio_stem.utils.config_checklist import get_configuration_checklist_data
from audio_stem.utils.karaoke_subtitles import (
	build_karaoke_ass_with_engine,
	is_karaoke_engine_available,
)
from audio_stem.utils.transcription_assets import write_transcript_json
from audio_stem.workers.karaoke_worker import process_karaoke_render

APP_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = APP_ROOT.parent / "pyproject.toml"


class TestAudioSeparationMilestone81(TestAudioSeparationMilestone8):
	def test_pyproject_does_not_list_pycaps_dependencies(self):
		text = PYPROJECT.read_text(encoding="utf-8")
		self.assertNotIn("pycaps-ai", text)
		self.assertNotIn("pycaps>=", text)
		self.assertNotIn("playwright", text)
		self.assertIn("karaoke_engine", text)

	def test_runtime_modules_do_not_import_pycaps_or_playwright(self):
		forbidden = ("import pycaps", "from pycaps", "import playwright", "from playwright")
		hits = []
		for path in APP_ROOT.rglob("*.py"):
			if "tests" in path.parts:
				continue
			text = path.read_text(encoding="utf-8", errors="ignore")
			for pattern in forbidden:
				if pattern in text:
					hits.append(f"{path}: {pattern}")
		self.assertEqual(hits, [])

	def test_karaoke_engine_import_checklist_item(self):
		self._enable_karaoke()
		items = {item["key"]: item for item in get_configuration_checklist_data()}
		self.assertIn("karaoke_engine_available", items)
		if is_karaoke_engine_available():
			self.assertEqual(items["karaoke_engine_available"]["status"], "ok")

	def test_completed_transcription_can_generate_ass(self):
		if not is_karaoke_engine_available():
			self.skipTest("karaoke_engine is not installed")
		self._enable_karaoke()
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.save(ignore_permissions=True)
		build_karaoke_ass_with_engine(job)
		job.save(ignore_permissions=True)
		self.assertTrue(job.karaoke_ass_file)

	def test_ass_file_is_private(self):
		if not is_karaoke_engine_available():
			self.skipTest("karaoke_engine is not installed")
		self._enable_karaoke()
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.save(ignore_permissions=True)
		build_karaoke_ass_with_engine(job)
		file_name = frappe.db.get_value("File", {"file_url": job.karaoke_ass_file}, "name")
		self.assertEqual(frappe.db.get_value("File", file_name, "is_private"), 1)

	def test_ass_generation_does_not_require_ffmpeg(self):
		if not is_karaoke_engine_available():
			self.skipTest("karaoke_engine is not installed")
		self._enable_karaoke()
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.save(ignore_permissions=True)
		with patch("audio_stem.utils.ffmpeg_media.is_ffmpeg_available", return_value=False):
			build_karaoke_ass_with_engine(job)
		self.assertTrue(job.karaoke_ass_file)

	def test_video_render_skipped_when_disabled(self):
		self._enable_karaoke()
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.save(ignore_permissions=True)
		with patch("audio_stem.workers.karaoke_worker.build_karaoke_ass_with_engine") as ass_mock:
			ass_mock.return_value = self._create_file().file_url
			with patch("audio_stem.workers.karaoke_worker.render_karaoke_video_with_engine") as render_mock:
				process_karaoke_render(job.name)
				render_mock.assert_not_called()
		job.reload()
		self.assertIn("Video render is disabled", job.karaoke_error or "")

	def test_video_render_requires_ffmpeg_when_enabled(self):
		self._enable_karaoke()
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_video_render_enabled = 1
		settings.save(ignore_permissions=True)
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.save(ignore_permissions=True)

		def _attach_ass(job, **kwargs):
			file_doc = self._create_file()
			job.karaoke_ass_file = file_doc.file_url
			return file_doc.file_url

		with patch("audio_stem.workers.karaoke_worker.build_karaoke_ass_with_engine", side_effect=_attach_ass):
			with patch(
				"audio_stem.workers.karaoke_worker.render_karaoke_video_with_engine",
				side_effect=frappe.ValidationError("ffmpeg missing"),
			):
				process_karaoke_render(job.name)
		job.reload()
		self.assertEqual(job.karaoke_status, "Completed")
		self.assertTrue(job.karaoke_ass_file)
		self.assertIn("Video render failed", job.karaoke_error or "")

	def test_job_payload_includes_karaoke_ass_file(self):
		self._enable_karaoke()
		job = self._create_completed_job()
		detail = get_job_detail(job.name)
		self.assertIn("karaoke_ass_file", detail)
		self.assertIn("karaoke_video_render_enabled", detail)

	def test_legacy_template_maps_to_style_preset(self):
		self._enable_karaoke()
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.save(ignore_permissions=True)
		with patch("audio_stem.api.separation.frappe.enqueue") as enqueue_mock:
			start_karaoke_render(job.name, template="hype")
		self.assertTrue(enqueue_mock.called)

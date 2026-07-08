# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Classic center karaoke style integration tests."""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe

from audio_stem.tests.test_milestone8 import SAMPLE_TRANSCRIPT, TestAudioSeparationMilestone8
from audio_stem.utils.karaoke_style_settings import (
	CLASSIC_CENTER_PRESET,
	build_classic_center_options_from_settings,
	validate_karaoke_style_settings,
)
from audio_stem.utils.karaoke_subtitles import (
	build_karaoke_ass_with_engine,
	get_karaoke_engine_style_args,
	is_karaoke_engine_available,
)
from audio_stem.workers.karaoke_worker import process_karaoke_render
from audio_stem.utils.transcription_assets import write_transcript_json

APP_ROOT = Path(__file__).resolve().parents[1]


class TestClassicCenterKaraokeStyle(TestAudioSeparationMilestone8):
	def setUp(self):
		super().setUp()
		if not is_karaoke_engine_available():
			self.skipTest("karaoke_engine is not installed")
		self._enable_karaoke()

	def _transcribed_job(self):
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		write_transcript_json(job, SAMPLE_TRANSCRIPT)
		job.transcript_text = SAMPLE_TRANSCRIPT["text"]
		job.save(ignore_permissions=True)
		return job

	def _enable_classic_center_settings(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_enabled = 1
		settings.karaoke_ass_enabled = 1
		settings.karaoke_style_preset = CLASSIC_CENTER_PRESET
		settings.karaoke_visible_lines = 3
		settings.karaoke_center_y_percent = 50
		settings.karaoke_line_gap = 90
		settings.karaoke_font_name = "Helvetica"
		settings.karaoke_font_size = 64
		settings.karaoke_primary_color = "#FFFFFF"
		settings.karaoke_highlight_color = "#3366FF"
		settings.karaoke_previous_line_color = "#3366FF"
		settings.karaoke_next_line_color = "#FFFFFF"
		settings.karaoke_outline_color = "#000000"
		settings.karaoke_shadow = 1
		settings.karaoke_outline = 3
		settings.save(ignore_permissions=True)
		frappe.db.commit()

	def test_style_preset_accepts_classic_center_3line(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_style_preset = CLASSIC_CENTER_PRESET
		validate_karaoke_style_settings(settings)
		settings.save(ignore_permissions=True)

	def test_invalid_hex_color_rejected(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_primary_color = "not-a-color"
		with self.assertRaises(frappe.ValidationError):
			validate_karaoke_style_settings(settings)

	def test_invalid_font_size_rejected(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_font_size = 10
		with self.assertRaises(frappe.ValidationError):
			validate_karaoke_style_settings(settings)

	def test_invalid_font_name_rejected(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_font_name = "Comic Sans MS"
		with self.assertRaises(frappe.ValidationError):
			validate_karaoke_style_settings(settings)

	def test_invalid_center_position_rejected(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_center_y_percent = 5
		with self.assertRaises(frappe.ValidationError):
			validate_karaoke_style_settings(settings)

	def test_audio_stem_passes_classic_center_options_to_engine(self):
		self._enable_classic_center_settings()
		style_args = get_karaoke_engine_style_args()
		self.assertIn("classic_center_options", style_args)
		options = style_args["classic_center_options"]
		self.assertEqual(options.font_name, "Helvetica")
		self.assertEqual(options.font_size, 64)
		self.assertEqual(options.highlight_color, "#3366FF")

	def test_ass_generation_with_classic_center_3line(self):
		self._enable_classic_center_settings()
		job = self._transcribed_job()

		try:
			build_karaoke_ass_with_engine(job)
		except Exception as exc:
			self.fail(f"build_karaoke_ass_with_engine raised: {exc!r}")

		self.assertTrue(
			job.karaoke_ass_file,
			msg=f"expected karaoke_ass_file, got {job.karaoke_ass_file!r}",
		)
		file_doc = frappe.get_doc("File", {"file_url": job.karaoke_ass_file})
		content = file_doc.get_content()
		if isinstance(content, bytes):
			content = content.decode("utf-8")
		self.assertIn("KaraokeActive", content)
		self.assertIn(r"{\an5\pos(", content)
		self.assertIn("&H00FF6633", content)
		self.assertIn("Helvetica,64,", content)

	def test_ass_generation_uses_selected_font(self):
		self._enable_classic_center_settings()
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_font_name = "Bebas Neue"
		settings.save(ignore_permissions=True)
		job = self._transcribed_job()
		build_karaoke_ass_with_engine(job)
		file_doc = frappe.get_doc("File", {"file_url": job.karaoke_ass_file})
		content = file_doc.get_content()
		if isinstance(content, bytes):
			content = content.decode("utf-8")
		self.assertIn("Bebas Neue,64,", content)

	def test_default_preset_style_args_exclude_classic_options(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_style_preset = "default_1080p"
		settings.save(ignore_permissions=True)
		style_args = get_karaoke_engine_style_args()
		self.assertNotIn("classic_center_options", style_args)
		self.assertEqual(style_args["style"].name, "Karaoke")

	def test_build_classic_center_options_from_settings(self):
		self._enable_classic_center_settings()
		options = build_classic_center_options_from_settings()
		self.assertEqual(options.visible_lines, 3)
		self.assertEqual(options.center_y_percent, 50.0)

	def test_mp4_render_still_works_with_classic_center(self):
		self._enable_classic_center_settings()
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_video_render_enabled = 1
		settings.save(ignore_permissions=True)

		job = self._transcribed_job()

		with patch("audio_stem.workers.karaoke_worker.render_karaoke_video_with_engine") as render_mock:
			render_mock.return_value = "/private/files/test.mp4"
			with patch("audio_stem.workers.karaoke_worker.build_karaoke_ass_with_engine") as ass_mock:
				ass_mock.return_value = "/private/files/test.ass"
				process_karaoke_render(job.name)
				ass_mock.assert_called_once()

	def test_no_forbidden_libraries_in_runtime_or_bundle(self):
		forbidden = ("pycaps", "playwright", "chromium")
		for root, _dirs, files in os.walk(APP_ROOT / "audio_stem"):
			for name in files:
				if not name.endswith(".py"):
					continue
				path = os.path.join(root, name)
				with open(path, "r", encoding="utf-8") as handle:
					content = handle.read().lower()
				for word in forbidden:
					if f"import {word}" in content or f"from {word}" in content:
						self.fail(f"Forbidden import {word} in {path}")

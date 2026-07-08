# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Karaoke per-job audio mode tests."""

from unittest.mock import patch

import frappe

from audio_stem.api.separation import start_karaoke_render
from audio_stem.tests.test_milestone8 import TestAudioSeparationMilestone8
from audio_stem.utils.files import resolve_frappe_file_path
from audio_stem.utils.karaoke_subtitles import (
	karaoke_audio_source_label,
	resolve_karaoke_audio_path,
	resolve_karaoke_use_instrumental,
)


class TestKaraokeAudioMode(TestAudioSeparationMilestone8):
	def test_auto_mode_uses_site_instrumental_setting(self):
		job = self._create_completed_job()
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_include_instrumental_audio = 1
		settings.save(ignore_permissions=True)
		job.karaoke_audio_mode = "Auto"
		job.save(ignore_permissions=True)

		self.assertTrue(resolve_karaoke_use_instrumental(job))
		audio_path = resolve_karaoke_audio_path(job)
		self.assertEqual(audio_path, resolve_frappe_file_path(job.instrumental_file))

	def test_original_mode_uses_original_song(self):
		job = self._create_completed_job()
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_include_instrumental_audio = 1
		settings.save(ignore_permissions=True)
		job.karaoke_audio_mode = "Original"
		job.save(ignore_permissions=True)

		self.assertFalse(resolve_karaoke_use_instrumental(job))
		self.assertEqual(karaoke_audio_source_label(job), "Original song")
		audio_path = resolve_karaoke_audio_path(job)
		self.assertEqual(audio_path, resolve_frappe_file_path(job.original_file))

	def test_instrumental_mode_forces_instrumental_track(self):
		job = self._create_completed_job()
		settings = frappe.get_single("Audio Separation Settings")
		settings.karaoke_include_instrumental_audio = 0
		settings.save(ignore_permissions=True)
		job.karaoke_audio_mode = "Instrumental"
		job.save(ignore_permissions=True)

		self.assertTrue(resolve_karaoke_use_instrumental(job))
		audio_path = resolve_karaoke_audio_path(job)
		self.assertEqual(audio_path, resolve_frappe_file_path(job.instrumental_file))

	def test_start_karaoke_render_persists_audio_mode(self):
		self._enable_karaoke()
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		job.save(ignore_permissions=True)

		with patch("audio_stem.api.separation.frappe.enqueue") as enqueue_mock:
			result = start_karaoke_render(job.name, karaoke_audio_mode="Original")
			enqueue_mock.assert_called_once()

		self.assertEqual(result["karaoke_audio_mode"], "Original")
		self.assertEqual(result["karaoke_audio_source_label"], "Original song")

# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

from unittest.mock import patch

import frappe

from audio_stem.api.separation import get_transcription_input_check
from audio_stem.tests.base import AudioStemTestCase, temporary_audio_settings
from audio_stem.utils.transcription_input_check import (
	AS_IS_LABEL,
	TRANSCODE_TARGET_LABEL,
	build_whisper_input_report,
	classify_transcription_source,
)


class TestWhisperInputCheck(AudioStemTestCase):
	def test_classify_vocal_source_prefers_local_file(self):
		job = self._create_completed_job()
		result = classify_transcription_source(job, "Vocal")
		self.assertEqual(result["source_kind"], "local_vocal_file")
		self.assertEqual(result["source_file_url"], job.vocal_file)
		self.assertTrue(result["source_file_name"])

	def test_report_preprocesses_when_enabled(self):
		job = self._create_completed_job()
		with temporary_audio_settings(transcription_audio_preprocess_enabled=1):
			report = build_whisper_input_report(job, "Vocal")
		self.assertTrue(report["will_transcode_for_whisper"])
		self.assertEqual(report["whisper_input_profile"], TRANSCODE_TARGET_LABEL)
		self.assertIsNotNone(report["source_size_mb"])

	def test_report_uses_as_is_when_preprocess_disabled(self):
		job = self._create_completed_job()
		with temporary_audio_settings(transcription_audio_preprocess_enabled=0):
			report = build_whisper_input_report(job, "Vocal")
		self.assertFalse(report["will_transcode_for_whisper"])
		self.assertEqual(report["whisper_input_profile"], AS_IS_LABEL)

	def test_report_flags_transcode_when_over_limit(self):
		job = self._create_completed_job()
		with patch(
			"audio_stem.utils.transcription_input_check.get_file_size_mb",
			return_value=30.0,
		):
			report = build_whisper_input_report(job, "Vocal")
		self.assertTrue(report["will_transcode_for_whisper"])
		self.assertEqual(report["whisper_input_profile"], TRANSCODE_TARGET_LABEL)
		self.assertEqual(
			report["whisper_input_audio"],
			{"codec": "mp3", "channels": 1, "sample_rate": 16000, "bitrate_kbps": 64},
		)

	def test_report_original_source(self):
		job = self._create_completed_job()
		report = build_whisper_input_report(job, "Original")
		self.assertEqual(report["transcription_source"], "Original")
		self.assertEqual(report["source_kind"], "local_original_file")
		self.assertEqual(report["source_file_url"], job.original_file)

	def test_api_returns_report_for_job_owner(self):
		job = self._create_completed_job()
		report = get_transcription_input_check(job.name, source="Vocal")
		self.assertEqual(report["job_name"], job.name)
		self.assertEqual(report["source_kind"], "local_vocal_file")

	def test_api_blocks_other_user(self):
		owner = self._ensure_user(f"whisper-owner-{frappe.generate_hash(length=6)}@example.com")
		other = self._ensure_user(f"whisper-other-{frappe.generate_hash(length=6)}@example.com")
		job = self._create_completed_job(user=owner)
		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			get_transcription_input_check(job.name, source="Vocal")

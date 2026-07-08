# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import frappe

from audio_stem.api.separation import get_page_settings, start_transcription
from audio_stem.integrations.openai_transcription_client import transcribe_with_whisper
from audio_stem.tests.base import AudioStemTestCase, temporary_audio_settings
from audio_stem.utils.transcription_assets import cleanup_temp_path, prepare_audio_for_whisper
from audio_stem.utils.transcription_quality import (
	UNRELIABLE_TRANSCRIPT_WARNING,
	apply_transcription_quality_diagnostics,
	build_transcription_prompt,
	build_whisper_style_primer,
	compute_transcription_quality_diagnostics,
	count_bad_word_timestamps,
	detect_prompt_echo,
	get_first_segment_start,
	has_suspicious_start_gap,
	merge_chunk_transcripts,
	offset_transcript_timestamps,
	resolve_default_transcription_source,
	validate_transcription_prompt_text,
)
from audio_stem.workers.transcription_worker import process_transcription

SAMPLE_TRANSCRIPT = {
	"text": "hello world",
	"language": "en",
	"duration": 180.0,
	"segments": [{"id": 0, "start": 0.0, "end": 2.5, "text": "hello world"}],
	"words": [
		{"word": "hello", "start": 0.0, "end": 1.0},
		{"word": "world", "start": 1.0, "end": 2.5},
	],
}


class TestGate101TranscriptionQuality(AudioStemTestCase):
	def _enable_openai(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.openai_enabled = 1
		settings.openai_api_key = "test-openai-key"
		settings.transcription_model = "whisper-1"
		settings.enable_word_timestamps = 1
		settings.save(ignore_permissions=True)

	def test_vocal_source_is_default_when_setting_enabled(self):
		with temporary_audio_settings(transcription_use_vocal_stem_by_default=1):
			self.assertEqual(resolve_default_transcription_source(), "Vocal")
		with temporary_audio_settings(transcription_use_vocal_stem_by_default=0):
			self.assertEqual(resolve_default_transcription_source(), "Original")

	def test_page_settings_expose_transcription_quality_fields(self):
		with temporary_audio_settings(
			transcription_prompt_enabled=1,
			transcription_use_vocal_stem_by_default=1,
			transcription_audio_preprocess_enabled=1,
			transcription_chunking_enabled=0,
		):
			payload = get_page_settings()
		self.assertTrue(payload["transcription_prompt_enabled"])
		self.assertTrue(payload["transcription_use_vocal_stem_by_default"])
		self.assertNotIn("test-openai-key", json.dumps(payload))

	def test_prompt_secret_validation(self):
		with self.assertRaises(frappe.ValidationError):
			validate_transcription_prompt_text("Use sk-1234567890abcdef for auth")

	def test_build_transcription_prompt_empty_by_default(self):
		with temporary_audio_settings(transcription_prompt_enabled=1, transcription_prompt_text=None):
			prompt = build_transcription_prompt()
		self.assertIsNone(prompt)

	def test_instruction_style_prompt_is_rejected(self):
		with self.assertRaises(frappe.ValidationError):
			build_whisper_style_primer(
				frappe._dict(
					{
						"transcription_prompt_enabled": 1,
						"transcription_prompt_text": "Preserve repeated lines, chorus lines, Malay and English words",
					}
				)
			)

	def test_preprocessing_uses_ffmpeg_helper_when_enabled(self):
		job = self._create_completed_job()
		path = tempfile.mktemp(suffix=".mp3")
		with open(path, "wb") as handle:
			handle.write(b"fake-audio")
		try:
			with patch(
				"audio_stem.utils.transcription_assets.preprocess_audio_for_transcription",
				return_value="/tmp/prepared.mp3",
			) as preprocess_mock:
				prepared_path, should_cleanup = prepare_audio_for_whisper(path)
			preprocess_mock.assert_called_once()
			self.assertEqual(prepared_path, "/tmp/prepared.mp3")
			self.assertTrue(should_cleanup)
		finally:
			os.unlink(path)

	def test_preprocessing_falls_back_safely_when_ffmpeg_fails(self):
		path = tempfile.mktemp(suffix=".mp3")
		with open(path, "wb") as handle:
			handle.write(b"fake-audio")
		try:
			with patch(
				"audio_stem.utils.transcription_assets.preprocess_audio_for_transcription",
				side_effect=Exception("ffmpeg failed"),
			):
				prepared_path, should_cleanup = prepare_audio_for_whisper(path)
			self.assertEqual(prepared_path, path)
			self.assertFalse(should_cleanup)
		finally:
			os.unlink(path)

	@patch("openai.OpenAI")
	def test_prompt_is_passed_to_openai_when_enabled(self, openai_mock):
		with temporary_audio_settings(
			transcription_prompt_enabled=1,
			transcription_prompt_text="Contoh lirik karaoke",
			openai_enabled=1,
			openai_api_key="test-openai-key",
		):
			client = MagicMock()
			openai_mock.return_value = client
			response = MagicMock()
			response.text = "hello"
			response.language = "en"
			response.duration = 1.0
			response.segments = []
			response.words = []
			response.model_dump.return_value = {
				"text": "hello",
				"language": "en",
				"duration": 1.0,
				"segments": [],
				"words": [],
			}
			client.audio.transcriptions.create.return_value = response

			with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
				tmp.write(b"audio")
				path = tmp.name

			transcribe_with_whisper(path)
			os.unlink(path)

			kwargs = client.audio.transcriptions.create.call_args.kwargs
			self.assertIn("prompt", kwargs)
			self.assertEqual(kwargs["prompt"], "Contoh lirik karaoke")

	@patch("openai.OpenAI")
	def test_language_is_passed_to_openai_when_set(self, openai_mock):
		self._enable_openai()
		client = MagicMock()
		openai_mock.return_value = client
		response = MagicMock()
		response.text = "hello"
		response.language = "ms"
		response.duration = 1.0
		response.segments = []
		response.words = []
		response.model_dump.return_value = {
			"text": "hello",
			"language": "ms",
			"duration": 1.0,
			"segments": [],
			"words": [],
		}
		client.audio.transcriptions.create.return_value = response

		with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
			tmp.write(b"audio")
			path = tmp.name

		transcribe_with_whisper(path, language="ms")
		os.unlink(path)

		kwargs = client.audio.transcriptions.create.call_args.kwargs
		self.assertEqual(kwargs["language"], "ms")

	def test_chunk_timestamps_are_offset_correctly(self):
		first = {
			"text": "hello",
			"duration": 10.0,
			"segments": [{"start": 0.0, "end": 2.0, "text": "hello"}],
			"words": [{"word": "hello", "start": 0.0, "end": 2.0}],
		}
		offset = offset_transcript_timestamps(first, 45.0)
		self.assertEqual(offset["segments"][0]["start"], 45.0)
		self.assertEqual(offset["words"][0]["start"], 45.0)

	def test_merged_chunk_transcript_preserves_words_and_segments(self):
		merged = merge_chunk_transcripts(
			[
				{
					"text": "hello",
					"language": "en",
					"duration": 45.0,
					"segments": [{"start": 0.0, "end": 2.0, "text": "hello"}],
					"words": [{"word": "hello", "start": 0.0, "end": 2.0}],
				},
				{
					"text": "world",
					"language": "en",
					"duration": 90.0,
					"segments": [{"start": 45.0, "end": 47.0, "text": "world"}],
					"words": [{"word": "world", "start": 45.0, "end": 47.0}],
				},
			]
		)
		self.assertEqual(merged["text"], "hello world")
		self.assertEqual(len(merged["segments"]), 2)
		self.assertEqual(len(merged["words"]), 2)

	def test_suspiciously_short_transcript_creates_warning(self):
		job = self._create_completed_job()
		job.duration_seconds = 180
		diagnostics = apply_transcription_quality_diagnostics(
			job,
			{"text": "hello", "duration": 180, "segments": [], "words": [{"word": "hello", "start": 0, "end": 1}]},
			requested_language="ms",
		)
		self.assertTrue(diagnostics["suspiciously_short_transcript"])
		self.assertTrue(diagnostics["transcription_quality_unreliable"])
		self.assertEqual(job.transcription_quality_warning, UNRELIABLE_TRANSCRIPT_WARNING)

	def test_prompt_echo_is_detected(self):
		echo_text = " ".join(
			["Preserve repeated lines, chorus lines, Malay and English words, and common filler words."] * 5
		)
		self.assertTrue(detect_prompt_echo(echo_text))

	def test_real_poor_217s_vocal_transcript_is_flagged(self):
		"""Regression for a 217s vocal stem with late start, bad timestamps, and javanese detection."""
		poor_transcript = {
			"text": "ing kono swara",
			"language": "javanese",
			"duration": 217.0,
			"segments": [
				{"start": 59.8, "end": 64.2, "text": "ing kono swara", "avg_logprob": -1.2},
			],
			"words": [
				{"word": "ing", "start": 59.8, "end": 59.8},
				{"word": "kono", "start": 60.1, "end": 60.5},
				{"word": "swara", "start": 61.0, "end": 61.0},
			],
		}
		job = self._create_completed_job()
		job.duration_seconds = 217
		job.transcription_language = "ms"

		diagnostics = compute_transcription_quality_diagnostics(
			poor_transcript,
			duration_seconds=217,
			requested_language="ms",
		)

		self.assertEqual(diagnostics["transcription_detected_language"], "jv")
		self.assertEqual(diagnostics["first_segment_start"], 59.8)
		self.assertEqual(diagnostics["bad_timestamp_count"], 2)
		self.assertTrue(diagnostics["suspicious_start_gap"])
		self.assertTrue(diagnostics["suspiciously_short_transcript"])
		self.assertTrue(diagnostics["language_mismatch"])
		self.assertTrue(diagnostics["transcription_quality_unreliable"])
		self.assertEqual(diagnostics["transcription_quality_warning"], UNRELIABLE_TRANSCRIPT_WARNING)

		apply_transcription_quality_diagnostics(job, poor_transcript, requested_language="ms")
		self.assertEqual(job.transcription_detected_language, "jv")
		self.assertEqual(job.transcription_first_segment_start, 59.8)
		self.assertEqual(job.transcription_bad_timestamp_count, 2)
		self.assertEqual(job.transcription_quality_warning, UNRELIABLE_TRANSCRIPT_WARNING)

	def test_zero_duration_word_timestamps_are_counted(self):
		words = [
			{"word": "a", "start": 1.0, "end": 1.0},
			{"word": "b", "start": 2.0, "end": 2.4},
		]
		self.assertEqual(count_bad_word_timestamps(words), 1)

	def test_suspicious_start_gap_detects_late_first_segment(self):
		self.assertTrue(has_suspicious_start_gap(217.0, 59.8))
		self.assertFalse(has_suspicious_start_gap(217.0, 5.0))

	def test_good_transcript_has_no_quality_warning(self):
		job = self._create_completed_job()
		job.duration_seconds = 5
		good_transcript = {
			"text": "hello world",
			"language": "en",
			"duration": 5.0,
			"segments": [{"start": 0.0, "end": 2.5, "text": "hello world"}],
			"words": [
				{"word": "hello", "start": 0.0, "end": 1.0},
				{"word": "world", "start": 1.0, "end": 2.5},
			],
		}
		diagnostics = apply_transcription_quality_diagnostics(
			job,
			good_transcript,
			requested_language="en",
		)
		self.assertFalse(diagnostics["transcription_quality_unreliable"])
		self.assertIsNone(job.transcription_quality_warning)
		self.assertEqual(job.transcription_detected_language, "en")
		self.assertEqual(job.transcription_first_segment_start, 0.0)
		self.assertEqual(job.transcription_bad_timestamp_count, 0)

	def test_temporary_files_are_cleaned_by_worker(self):
		self._enable_openai()
		job = self._create_completed_job()
		temp_path = tempfile.mktemp(suffix=".mp3")
		with open(temp_path, "wb") as handle:
			handle.write(b"audio")

		with patch(
			"audio_stem.workers.transcription_worker.resolve_transcription_source_path",
			return_value=temp_path,
		), patch(
			"audio_stem.workers.transcription_worker.prepare_audio_for_whisper",
			return_value=(temp_path, False),
		), patch(
			"audio_stem.workers.transcription_worker.transcribe_with_whisper",
			return_value=SAMPLE_TRANSCRIPT,
		):
			process_transcription(job.name, source="Vocal", language="en")

		self.assertFalse(os.path.exists(temp_path))

	def test_completed_transcription_can_be_retried(self):
		self._enable_openai()
		job = self._create_completed_job()
		job.transcription_status = "Completed"
		job.save(ignore_permissions=True)

		with patch("audio_stem.api.separation.frappe.enqueue"):
			result = start_transcription(job.name, source="Vocal", language="en")

		self.assertEqual(result["transcription_status"], "Queued")

	def test_cleanup_temp_path_removes_file(self):
		path = tempfile.mktemp(suffix=".mp3")
		with open(path, "wb") as handle:
			handle.write(b"x")
		cleanup_temp_path(path, should_cleanup=True)
		self.assertFalse(os.path.exists(path))

# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import frappe
import requests

from audio_stem.api.separation import get_page_settings, start_transcription
from audio_stem.integrations.elevenlabs_scribe_client import (
	normalize_scribe_response,
	transcribe_with_scribe,
)
from audio_stem.integrations.transcription_provider import (
	PROVIDER_ELEVENLABS,
	PROVIDER_OPENAI,
	get_transcription_provider,
	transcribe_audio,
)
from audio_stem.tests.base import AudioStemTestCase, temporary_audio_settings
from audio_stem.utils.config_checklist import get_configuration_checklist_data
from audio_stem.utils.scribe_keyterms import parse_keyterms, validate_keyterms
from audio_stem.utils.scribe_segments import group_words_into_segments
from audio_stem.utils.transcription_assets import estimate_transcription_cost, write_raw_provider_json
from audio_stem.utils.transcription_quality import has_requested_language_mismatch
from audio_stem.workers.transcription_worker import process_transcription

SCRIBE_RESPONSE = {
	"language_code": "msa",
	"language_probability": 0.91,
	"text": "hello dunia",
	"words": [
		{"text": "hello", "start": 0.0, "end": 0.5, "type": "word"},
		{"text": " ", "start": 0.5, "end": 0.5, "type": "spacing"},
		{"text": "dunia", "start": 0.5, "end": 1.0, "type": "word"},
	],
}


class TestGate103TranscriptionProviders(AudioStemTestCase):
	def _enable_openai(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.openai_enabled = 1
		settings.openai_api_key = "test-openai-key"
		settings.transcription_provider = PROVIDER_OPENAI
		settings.save(ignore_permissions=True)

	def _enable_elevenlabs(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.transcription_provider = PROVIDER_ELEVENLABS
		settings.elevenlabs_scribe_enabled = 1
		settings.elevenlabs_api_key = "test-elevenlabs-key"
		settings.elevenlabs_scribe_model = "scribe_v2"
		settings.save(ignore_permissions=True)

	def test_default_provider_is_openai_whisper(self):
		with temporary_audio_settings(transcription_provider="OpenAI Whisper"):
			self.assertEqual(get_transcription_provider(), PROVIDER_OPENAI)

	def test_page_settings_do_not_expose_api_keys(self):
		with temporary_audio_settings(
			openai_enabled=1,
			openai_api_key="test-openai-key",
			elevenlabs_scribe_enabled=1,
			elevenlabs_api_key="test-elevenlabs-key",
		):
			payload = get_page_settings()
		serialized = json.dumps(payload)
		self.assertNotIn("test-openai-key", serialized)
		self.assertNotIn("test-elevenlabs-key", serialized)
		self.assertIn("transcription_provider", payload)

	def test_elevenlabs_provider_requires_enabled_and_key(self):
		from audio_stem.integrations.transcription_provider import is_transcription_provider_configured

		with temporary_audio_settings(
			transcription_provider=PROVIDER_ELEVENLABS,
			elevenlabs_scribe_enabled=0,
			elevenlabs_api_key="test-elevenlabs-key",
		):
			self.assertFalse(is_transcription_provider_configured(PROVIDER_ELEVENLABS))
		with temporary_audio_settings(
			transcription_provider=PROVIDER_ELEVENLABS,
			elevenlabs_scribe_enabled=1,
			elevenlabs_api_key="test-elevenlabs-key",
		):
			self.assertTrue(is_transcription_provider_configured(PROVIDER_ELEVENLABS))

	def test_invalid_scribe_model_rejected(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.elevenlabs_scribe_model = "bad-model"
		with self.assertRaises(frappe.ValidationError):
			settings.save(ignore_permissions=True)

	def test_keyterms_validation_rejects_unsupported_characters(self):
		with self.assertRaises(frappe.ValidationError):
			validate_keyterms(["bad<term"])

	def test_parse_keyterms_splits_lines(self):
		terms = parse_keyterms("chorus phrase\nnama tempat")
		self.assertEqual(terms, ["chorus phrase", "nama tempat"])

	def test_normalize_scribe_response_maps_words_and_segments(self):
		normalized = normalize_scribe_response(SCRIBE_RESPONSE, duration_fallback=30.0)
		self.assertEqual(normalized["language"], "msa")
		self.assertEqual(normalized["language_probability"], 0.91)
		self.assertEqual(len(normalized["words"]), 2)
		self.assertEqual(normalized["words"][0]["word"], "hello")
		self.assertTrue(normalized["segments"])

	def test_group_words_into_segments_builds_text(self):
		segments = group_words_into_segments(
			[
				{"word": "hello", "start": 0.0, "end": 0.5},
				{"word": "dunia", "start": 0.5, "end": 1.0},
			]
		)
		self.assertEqual(segments[0]["text"], "hello dunia")

	def test_language_mismatch_accepts_ms_and_msa(self):
		self.assertFalse(has_requested_language_mismatch("ms", "msa"))

	def test_scribe_cost_includes_keyterm_addon(self):
		with temporary_audio_settings(
			transcription_provider=PROVIDER_ELEVENLABS,
			elevenlabs_cost_per_hour_usd=0.22,
			elevenlabs_keyterm_cost_per_hour_usd=0.05,
		):
			base = estimate_transcription_cost(3600, provider=PROVIDER_ELEVENLABS, keyterms_used=False)
			with_terms = estimate_transcription_cost(3600, provider=PROVIDER_ELEVENLABS, keyterms_used=True)
		self.assertEqual(base, 0.22)
		self.assertEqual(with_terms, 0.27)

	def test_openai_cost_behavior_preserved(self):
		with temporary_audio_settings(transcription_cost_per_minute_usd=0.06):
			cost = estimate_transcription_cost(120, provider=PROVIDER_OPENAI)
		self.assertEqual(cost, 0.12)

	@patch("audio_stem.integrations.elevenlabs_scribe_client.requests.post")
	def test_scribe_client_builds_expected_request(self, post_mock):
		self._enable_elevenlabs()
		response = MagicMock()
		response.status_code = 200
		response.json.return_value = SCRIBE_RESPONSE
		post_mock.return_value = response

		with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
			tmp.write(b"audio")
			path = tmp.name

		result = transcribe_with_scribe(path, language="msa", keyterms=["chorus"])
		os.unlink(path)

		kwargs = post_mock.call_args.kwargs
		self.assertEqual(kwargs["headers"]["xi-api-key"], "test-elevenlabs-key")
		self.assertEqual(kwargs["data"]["model_id"], "scribe_v2")
		self.assertEqual(kwargs["data"]["language_code"], "msa")
		self.assertEqual(kwargs["data"]["timestamps_granularity"], "word")
		self.assertEqual(kwargs["timeout"], 900)
		self.assertIn("keyterms", kwargs["data"])
		self.assertEqual(result["provider"], "ElevenLabs Scribe")

	@patch("audio_stem.integrations.elevenlabs_scribe_client.requests.post")
	def test_scribe_safe_error_does_not_expose_api_key(self, post_mock):
		self._enable_elevenlabs()
		post_mock.side_effect = requests.RequestException("xi-api-key=secret")

		with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
			tmp.write(b"audio")
			path = tmp.name
		with self.assertRaises(frappe.ValidationError) as ctx:
			transcribe_with_scribe(path)
		os.unlink(path)
		self.assertNotIn("secret", str(ctx.exception).lower())

	@patch("audio_stem.integrations.transcription_provider.transcribe_with_scribe")
	def test_provider_abstraction_calls_scribe_when_selected(self, scribe_mock):
		self._enable_elevenlabs()
		scribe_mock.return_value = normalize_scribe_response(SCRIBE_RESPONSE, duration_fallback=1.0)
		with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
			tmp.write(b"audio")
			path = tmp.name
		result = transcribe_audio(path, provider=PROVIDER_ELEVENLABS, keyterms=["chorus"])
		os.unlink(path)
		scribe_mock.assert_called_once()
		self.assertEqual(result["provider"], PROVIDER_ELEVENLABS)

	@patch("audio_stem.integrations.transcription_provider.transcribe_with_whisper")
	def test_provider_abstraction_calls_openai_when_selected(self, whisper_mock):
		self._enable_openai()
		whisper_mock.return_value = {
			"text": "hello",
			"language": "en",
			"duration": 1.0,
			"segments": [],
			"words": [],
		}
		with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as tmp:
			tmp.write(b"audio")
			path = tmp.name
		result = transcribe_audio(path, provider=PROVIDER_OPENAI)
		os.unlink(path)
		whisper_mock.assert_called_once()
		self.assertEqual(result["provider"], PROVIDER_OPENAI)

	def test_checklist_does_not_expose_elevenlabs_api_key(self):
		self._enable_elevenlabs()
		payload = json.dumps(get_configuration_checklist_data())
		self.assertNotIn("test-elevenlabs-key", payload)

	def test_worker_generates_assets_for_scribe_result(self):
		self._enable_elevenlabs()
		job = self._create_completed_job()
		normalized = normalize_scribe_response(SCRIBE_RESPONSE, duration_fallback=job.duration_seconds)

		with patch(
			"audio_stem.workers.transcription_worker.resolve_transcription_source_path",
			return_value="/tmp/fake.mp3",
		), patch(
			"audio_stem.workers.transcription_worker.prepare_audio_for_whisper",
			return_value=("/tmp/fake.mp3", False),
		), patch(
			"audio_stem.workers.transcription_worker.transcribe_audio",
			return_value=normalized,
		):
			process_transcription(job.name, provider=PROVIDER_ELEVENLABS, language="ms")

		job.reload()
		self.assertEqual(job.transcription_status, "Completed")
		self.assertEqual(job.transcription_provider, PROVIDER_ELEVENLABS)
		self.assertTrue(job.transcript_json_file)
		self.assertTrue(job.transcript_srt_file)
		self.assertTrue(job.transcript_vtt_file)
		self.assertEqual(job.transcription_detected_language, "msa")

	def test_write_raw_provider_json_attaches_private_file(self):
		job = self._create_completed_job()
		write_raw_provider_json(job, SCRIBE_RESPONSE)
		self.assertTrue(job.transcription_raw_provider_json_file)

	def test_start_transcription_accepts_provider_override(self):
		self._enable_elevenlabs()
		job = self._create_completed_job()
		with patch("audio_stem.api.separation.frappe.enqueue"):
			result = start_transcription(job.name, provider=PROVIDER_ELEVENLABS, source="Vocal")
		self.assertEqual(result["transcription_status"], "Queued")

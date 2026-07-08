# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
from unittest.mock import MagicMock, patch

import frappe

from audio_stem.api.separation import (
	accept_llm_suggestion_as_manual_draft,
	get_llm_suggestion,
	get_page_settings,
	start_llm_transcript_suggestion,
	suggest_scribe_keyterms,
)
from audio_stem.integrations.llm_provider import is_llm_assistant_enabled, run_llm_json_task
from audio_stem.integrations.wavespeed_llm_client import (
	chat_completions_json,
	is_wavespeed_llm_configured,
	resolve_wavespeed_llm_base_url,
)
from audio_stem.tests.base import AudioStemTestCase, temporary_audio_settings
from audio_stem.utils.config_checklist import get_configuration_checklist_data
from audio_stem.utils.lyric_assistant import create_manual_draft_from_llm_suggestion
from audio_stem.workers.llm_assistant_worker import process_llm_suggestion

MOCK_LLM_REPAIR = {
	"task": "repair_transcript_text",
	"suggested_text": "hello dunia yang indah",
	"suggested_segments": [{"text": "hello dunia", "start": 0.0, "end": 1.0}, {"text": "yang indah", "start": 1.0, "end": 2.0}],
	"warnings": ["uncertain local word"],
	"confidence_notes": ["review chorus"],
	"requires_manual_review": True,
	"provider": "WaveSpeed LLM",
	"model": "deepseek/deepseek-v4-flash",
	"input_tokens": 120,
	"output_tokens": 45,
}

MOCK_LLM_KEYTERMS = {
	"task": "suggest_scribe_keyterms",
	"keyterms": ["dunia", "chorus phrase"],
	"warnings": [],
	"confidence_notes": [],
	"requires_manual_review": True,
	"provider": "WaveSpeed LLM",
	"model": "deepseek/deepseek-v4-flash",
	"input_tokens": 80,
	"output_tokens": 20,
}


class TestGate104LlmLyricAssistant(AudioStemTestCase):
	def _enable_llm(self):
		settings = frappe.get_single("Audio Separation Settings")
		settings.llm_assistant_enabled = 1
		settings.wavespeed_llm_api_key = "test-wavespeed-llm-key"
		settings.wavespeed_llm_base_url = "https://llm.wavespeed.ai/v1"
		settings.wavespeed_llm_model = "deepseek/deepseek-v4-flash"
		settings.save(ignore_permissions=True)

	def _create_transcribed_job(self):
		job = self._create_completed_job()
		transcript = {
			"text": "hello dunia",
			"language": "ms",
			"duration": 2.0,
			"segments": [
				{
					"text": "hello dunia",
					"start": 0.0,
					"end": 2.0,
					"words": [
						{"text": "hello", "start": 0.0, "end": 1.0},
						{"text": "dunia", "start": 1.0, "end": 2.0},
					],
				}
			],
			"words": [],
		}
		from audio_stem.utils.transcription_assets import write_transcript_json

		job.transcription_status = "Completed"
		job.transcript_text = transcript["text"]
		write_transcript_json(job, transcript)
		job.save(ignore_permissions=True)
		return job

	def test_llm_disabled_blocks_api_safely(self):
		job = self._create_transcribed_job()
		with temporary_audio_settings(llm_assistant_enabled=0):
			with self.assertRaises(frappe.ValidationError):
				start_llm_transcript_suggestion(job.name)

	def test_owner_can_request_suggestion(self):
		self._enable_llm()
		job = self._create_transcribed_job()
		with patch("frappe.enqueue"):
			result = start_llm_transcript_suggestion(job.name, task="repair_transcript")
		self.assertEqual(result["llm_suggestion_status"], "Queued")

	def test_another_user_cannot_request_suggestion(self):
		self._enable_llm()
		job = self._create_transcribed_job()
		other = self._ensure_user("llm-other@test.com")
		frappe.set_user(other)
		with self.assertRaises(frappe.PermissionError):
			start_llm_transcript_suggestion(job.name)

	def test_system_manager_can_request_suggestion(self):
		self._enable_llm()
		job = self._create_transcribed_job()
		frappe.set_user("Administrator")
		with patch("frappe.enqueue"):
			result = start_llm_transcript_suggestion(job.name, task="repair_transcript")
		self.assertEqual(result["llm_suggestion_status"], "Queued")

	@patch("openai.OpenAI")
	def test_wavespeed_llm_client_uses_base_url_and_model(self, openai_mock):
		self._enable_llm()
		client_instance = MagicMock()
		openai_mock.return_value = client_instance
		response = MagicMock()
		response.choices = [MagicMock(message=MagicMock(content=json.dumps({"keyterms": ["dunia"]})))]
		response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
		response.model_dump.return_value = {"ok": True}
		client_instance.chat.completions.create.return_value = response

		result = chat_completions_json(
			[{"role": "user", "content": "test"}],
			model="deepseek/deepseek-v4-flash",
		)

		openai_mock.assert_called_once()
		kwargs = openai_mock.call_args.kwargs
		self.assertEqual(kwargs["base_url"], "https://llm.wavespeed.ai/v1")
		self.assertEqual(result["model"], "deepseek/deepseek-v4-flash")

	def test_page_settings_and_checklist_do_not_expose_api_key(self):
		self._enable_llm()
		with temporary_audio_settings(wavespeed_llm_api_key="secret-llm-key"):
			payload = get_page_settings()
			checklist = json.dumps(get_configuration_checklist_data())
		serialized = json.dumps(payload) + checklist
		self.assertNotIn("secret-llm-key", serialized)
		self.assertIn("llm_assistant_enabled", payload)

	@patch("audio_stem.integrations.llm_provider.chat_completions_json")
	def test_malformed_json_response_rejected_safely(self, chat_mock):
		self._enable_llm()
		chat_mock.side_effect = frappe.ValidationError("WaveSpeed LLM returned invalid JSON. Please try again.")
		with self.assertRaises(frappe.ValidationError) as ctx:
			run_llm_json_task("suggest_scribe_keyterms", {"lyrics_text": "hello dunia"})
		self.assertNotIn("test-wavespeed-llm-key", str(ctx.exception))

	@patch("audio_stem.integrations.llm_provider.chat_completions_json")
	def test_suggestion_does_not_overwrite_original_transcript(self, chat_mock):
		self._enable_llm()
		job = self._create_transcribed_job()
		original_text = job.transcript_text
		chat_mock.return_value = {
			"parsed": {
				"suggested_text": MOCK_LLM_REPAIR["suggested_text"],
				"suggested_segments": MOCK_LLM_REPAIR["suggested_segments"],
				"warnings": MOCK_LLM_REPAIR["warnings"],
				"confidence_notes": MOCK_LLM_REPAIR["confidence_notes"],
				"requires_manual_review": True,
			},
			"provider": "WaveSpeed LLM",
			"model": "deepseek/deepseek-v4-flash",
			"input_tokens": 120,
			"output_tokens": 45,
			"raw_response": {},
		}
		process_llm_suggestion(job.name, task="repair_transcript_text")
		job.reload()
		self.assertEqual(job.transcript_text, original_text)
		self.assertEqual(job.llm_suggestion_status, "Completed")

	@patch("audio_stem.integrations.llm_provider.chat_completions_json")
	def test_accepting_suggestion_creates_manual_draft_not_auto_approved(self, chat_mock):
		self._enable_llm()
		job = self._create_transcribed_job()
		chat_mock.return_value = {
			"parsed": {
				"suggested_text": MOCK_LLM_REPAIR["suggested_text"],
				"suggested_segments": MOCK_LLM_REPAIR["suggested_segments"],
				"warnings": MOCK_LLM_REPAIR["warnings"],
				"confidence_notes": MOCK_LLM_REPAIR["confidence_notes"],
				"requires_manual_review": True,
			},
			"provider": "WaveSpeed LLM",
			"model": "deepseek/deepseek-v4-flash",
			"input_tokens": 120,
			"output_tokens": 45,
			"raw_response": {},
		}
		process_llm_suggestion(job.name, task="repair_transcript_text")
		job.reload()
		result = accept_llm_suggestion_as_manual_draft(job.name)
		job.reload()
		self.assertEqual(job.manual_transcript_status, "Draft")
		self.assertTrue(job.manual_transcript_json_file)
		self.assertEqual(result["manual_transcript_status"], "Draft")
		self.assertEqual(job.transcript_text, "hello dunia")

	@patch("audio_stem.integrations.llm_provider.chat_completions_json")
	def test_suggest_keyterms_returns_validated_keyterms(self, chat_mock):
		self._enable_llm()
		job = self._create_transcribed_job()
		chat_mock.return_value = {
			"parsed": {"keyterms": MOCK_LLM_KEYTERMS["keyterms"], "requires_manual_review": True},
			"provider": "WaveSpeed LLM",
			"model": "deepseek/deepseek-v4-flash",
			"input_tokens": 80,
			"output_tokens": 20,
			"raw_response": {},
		}
		result = suggest_scribe_keyterms(job.name, lyrics_text="hello dunia chorus phrase")
		self.assertIn("dunia", result["keyterms"])

	@patch("audio_stem.integrations.llm_provider.chat_completions_json")
	def test_token_usage_and_cost_stored_when_available(self, chat_mock):
		self._enable_llm()
		job = self._create_transcribed_job()
		chat_mock.return_value = {
			"parsed": {
				"suggested_text": MOCK_LLM_REPAIR["suggested_text"],
				"suggested_segments": MOCK_LLM_REPAIR["suggested_segments"],
				"warnings": [],
				"confidence_notes": [],
				"requires_manual_review": True,
			},
			"provider": "WaveSpeed LLM",
			"model": "deepseek/deepseek-v4-flash",
			"input_tokens": 120,
			"output_tokens": 45,
			"raw_response": {},
		}
		process_llm_suggestion(job.name, task="repair_transcript_text")
		job.reload()
		self.assertEqual(job.llm_input_tokens, 120)
		self.assertEqual(job.llm_output_tokens, 45)

	def test_is_llm_assistant_enabled_requires_key(self):
		with temporary_audio_settings(
			llm_assistant_enabled=1,
			wavespeed_llm_api_key="",
		):
			self.assertFalse(is_llm_assistant_enabled())
		with temporary_audio_settings(
			llm_assistant_enabled=1,
			wavespeed_llm_api_key="test-wavespeed-llm-key",
		):
			self.assertTrue(is_llm_assistant_enabled())

	def test_create_manual_draft_preserves_asr_timings(self):
		self._enable_llm()
		job = self._create_transcribed_job()
		draft = create_manual_draft_from_llm_suggestion(job, MOCK_LLM_REPAIR)
		segment = draft["transcript"]["segments"][0]
		self.assertEqual(segment["start"], 0.0)
		self.assertEqual(segment["end"], 2.0)
		self.assertIn("hello", segment["text"])

	def test_get_llm_suggestion_payload(self):
		self._enable_llm()
		job = self._create_transcribed_job()
		payload = get_llm_suggestion(job.name)
		self.assertEqual(payload["llm_suggestion_status"], "Not Started")

	def test_wavespeed_llm_configured_checks(self):
		with temporary_audio_settings(
			wavespeed_llm_api_key="test-wavespeed-llm-key",
			wavespeed_llm_base_url="https://llm.wavespeed.ai/v1",
			wavespeed_llm_model="deepseek/deepseek-v4-flash",
		):
			self.assertTrue(is_wavespeed_llm_configured())
			self.assertEqual(resolve_wavespeed_llm_base_url(), "https://llm.wavespeed.ai/v1")

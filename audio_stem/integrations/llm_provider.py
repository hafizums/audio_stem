# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.integrations.wavespeed_llm_client import (
	chat_completions_json,
	is_wavespeed_llm_configured,
	resolve_wavespeed_llm_model,
)
from audio_stem.utils.limits import get_settings

LLM_PROVIDER_WAVESPEED = "WaveSpeed LLM"

SUPPORTED_TASKS = {
	"repair_transcript_text",
	"repair_transcript",
	"split_lyrics_lines",
	"suggest_scribe_keyterms",
	"explain_transcription_quality",
}


def is_llm_assistant_enabled(settings=None) -> bool:
	settings = settings or get_settings()
	return bool(cint(settings.llm_assistant_enabled)) and is_wavespeed_llm_configured(settings)


def get_llm_assistant_blocked_reason(settings=None) -> str | None:
	settings = settings or get_settings()
	if not cint(settings.llm_assistant_enabled):
		return _("LLM lyric assistant is disabled.")
	if not is_wavespeed_llm_configured(settings):
		return _("WaveSpeed LLM is not configured.")
	return None


def normalize_task_name(task_name: str) -> str:
	task = (task_name or "").strip()
	if task == "repair_transcript":
		return "repair_transcript_text"
	return task


def estimate_llm_cost_usd(input_tokens: int, output_tokens: int, settings=None) -> float:
	settings = settings or get_settings()
	input_rate = flt(settings.wavespeed_llm_input_cost_per_million_tokens)
	output_rate = flt(settings.wavespeed_llm_output_cost_per_million_tokens)
	if input_rate <= 0 and output_rate <= 0:
		return 0.0
	return round(
		(cint(input_tokens) / 1_000_000) * input_rate + (cint(output_tokens) / 1_000_000) * output_rate,
		6,
	)


def normalize_llm_task_result(task_name: str, provider_result: dict) -> dict:
	task = normalize_task_name(task_name)
	parsed = provider_result.get("parsed") or {}

	suggested_text = (parsed.get("suggested_text") or parsed.get("text") or "").strip()
	suggested_segments = parsed.get("suggested_segments") or parsed.get("segments") or []
	keyterms = parsed.get("keyterms") or []
	warnings = parsed.get("warnings") or []
	confidence_notes = parsed.get("confidence_notes") or parsed.get("notes") or []
	requires_manual_review = bool(parsed.get("requires_manual_review", True))

	if task == "suggest_scribe_keyterms" and not keyterms:
		keyterms = parsed.get("terms") or parsed.get("key_terms") or []

	if task == "explain_transcription_quality":
		if not suggested_text and parsed.get("summary"):
			suggested_text = str(parsed.get("summary")).strip()

	if not isinstance(suggested_segments, list):
		suggested_segments = []
	if not isinstance(keyterms, list):
		keyterms = []
	if not isinstance(warnings, list):
		warnings = [str(warnings)]
	if not isinstance(confidence_notes, list):
		confidence_notes = [str(confidence_notes)]

	return {
		"task": task,
		"suggested_text": suggested_text,
		"suggested_segments": suggested_segments,
		"keyterms": [str(term).strip() for term in keyterms if str(term).strip()],
		"warnings": [str(item).strip() for item in warnings if str(item).strip()],
		"confidence_notes": [str(item).strip() for item in confidence_notes if str(item).strip()],
		"requires_manual_review": requires_manual_review,
		"raw_response": provider_result.get("raw_response") or parsed,
		"provider": provider_result.get("provider") or LLM_PROVIDER_WAVESPEED,
		"model": provider_result.get("model"),
		"input_tokens": cint(provider_result.get("input_tokens")),
		"output_tokens": cint(provider_result.get("output_tokens")),
	}


def run_llm_json_task(
	task_name: str,
	payload: dict,
	*,
	use_reasoning_model: bool = False,
	settings=None,
) -> dict:
	settings = settings or get_settings()
	task = normalize_task_name(task_name)
	if task not in SUPPORTED_TASKS:
		frappe.throw(_("Unsupported LLM task: {0}").format(task_name), frappe.ValidationError)

	blocked = get_llm_assistant_blocked_reason(settings)
	if blocked:
		frappe.throw(blocked, frappe.ValidationError)

	from audio_stem.utils.lyric_assistant import build_llm_messages

	messages = build_llm_messages(task, payload, settings=settings)
	provider_result = chat_completions_json(
		messages,
		use_reasoning_model=use_reasoning_model,
		settings=settings,
	)
	return normalize_llm_task_result(task, provider_result)

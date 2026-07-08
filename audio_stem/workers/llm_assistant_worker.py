# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import traceback

import frappe
from frappe.utils import now_datetime

from audio_stem.integrations.llm_provider import normalize_task_name, run_llm_json_task
from audio_stem.utils.audit_log import log_audit
from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.limits import get_settings
from audio_stem.utils.lyric_assistant import (
	build_transcript_repair_payload,
	explain_transcription_quality,
	fail_llm_suggestion,
	save_llm_suggestion,
	split_reference_lyrics_for_karaoke,
	suggest_keyterms_from_lyrics,
	suggest_transcript_corrections,
)


def process_llm_suggestion(
	name: str,
	task: str,
	lyrics_text: str | None = None,
	language_hint: str | None = None,
):
	job = frappe.get_doc("Audio Separation Job", name)
	settings = get_settings()
	task_name = normalize_task_name(task)

	job.llm_suggestion_status = "Processing"
	job.llm_suggestion_task = task_name
	job.llm_suggestion_error = None
	job.save(ignore_permissions=True)

	try:
		if task_name == "repair_transcript_text":
			result = suggest_transcript_corrections(
				job,
				options={"language_hint": language_hint, "reference_lyrics": lyrics_text},
			)
		elif task_name == "split_lyrics_lines":
			text = (lyrics_text or job.transcript_text or "").strip()
			suggestion = split_reference_lyrics_for_karaoke(text, language_hint=language_hint)
			save_llm_suggestion(job, suggestion)
			result = suggestion
		elif task_name == "suggest_scribe_keyterms":
			text = (lyrics_text or job.transcript_text or "").strip()
			terms = suggest_keyterms_from_lyrics(text, language_hint=language_hint)
			suggestion = {
				"task": task_name,
				"keyterms": terms,
				"suggested_text": "\n".join(terms),
				"warnings": [],
				"confidence_notes": [],
				"requires_manual_review": True,
				"provider": "WaveSpeed LLM",
				"model": settings.wavespeed_llm_model,
			}
			save_llm_suggestion(job, suggestion)
			result = suggestion
		elif task_name == "explain_transcription_quality":
			result = explain_transcription_quality(job)
		else:
			payload = build_transcript_repair_payload(job, options={"language_hint": language_hint})
			suggestion = run_llm_json_task(task_name, payload)
			save_llm_suggestion(job, suggestion)
			result = suggestion

		log_audit(
			"Complete LLM Suggestion",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message=f"Completed LLM suggestion ({task_name}).",
			metadata={"task": task_name},
		)
		return result
	except Exception as exc:
		frappe.log_error(title="LLM Suggestion Failed", message=traceback.format_exc())
		fail_llm_suggestion(job, safe_error_message(exc))
		log_audit(
			"Fail LLM Suggestion",
			reference_doctype=job.doctype,
			reference_name=job.name,
			message=f"LLM suggestion failed ({task_name}).",
			metadata={"task": task_name},
		)
		raise

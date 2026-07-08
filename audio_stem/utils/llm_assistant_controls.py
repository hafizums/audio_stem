# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils.background_jobs import is_job_enqueued

from audio_stem.integrations.llm_provider import get_llm_assistant_blocked_reason, is_llm_assistant_enabled
from audio_stem.utils.limits import get_settings

LLM_ACTIVE_STATUSES = ("Queued", "Processing")
LLM_STARTABLE = ("Not Started", "Failed", "Completed")


def llm_queue_is_stale(job) -> bool:
	status = job.get("llm_suggestion_status") or "Not Started"
	return status == "Queued" and not is_job_enqueued(f"audio_llm_suggestion:{job.name}")


def can_start_llm_suggestion(job, settings=None) -> tuple[bool, str | None]:
	settings = settings or get_settings()
	if not is_llm_assistant_enabled(settings):
		return False, get_llm_assistant_blocked_reason(settings)
	if (job.transcription_status or "Not Started") != "Completed":
		return False, _("Completed transcription is required before using the LLM assistant.")
	if not job.transcript_json_file:
		return False, _("Original transcript is not available.")

	status = job.get("llm_suggestion_status") or "Not Started"
	if llm_queue_is_stale(job):
		return True, None
	if status in LLM_ACTIVE_STATUSES:
		return False, _("LLM suggestion is already in progress.")
	if status not in LLM_STARTABLE:
		return False, _("LLM suggestion cannot be started for this job.")
	return True, None


def enqueue_llm_suggestion(job, *, task: str, lyrics_text: str | None = None, language_hint: str | None = None):
	job.llm_suggestion_status = "Queued"
	job.llm_suggestion_error = None
	job.llm_suggestion_task = task
	job.save(ignore_permissions=True)
	frappe.enqueue(
		"audio_stem.workers.llm_assistant_worker.process_llm_suggestion",
		queue="long",
		job_id=f"audio_llm_suggestion:{job.name}",
		name=job.name,
		task=task,
		lyrics_text=lyrics_text,
		language_hint=language_hint,
	)

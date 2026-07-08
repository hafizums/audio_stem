# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Track when transcription/karaoke assets no longer match current separation outputs."""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

DEFAULT_DOWNSTREAM_STALE_REASON = _(
	"Audio separation was regenerated. Transcription and karaoke assets should be regenerated."
)


def job_had_downstream_assets(job) -> bool:
	transcription_status = job.get("transcription_status") or "Not Started"
	karaoke_status = job.get("karaoke_status") or "Not Started"
	manual_status = job.get("manual_transcript_status") or "Not Started"
	return any(
		[
			transcription_status != "Not Started",
			karaoke_status != "Not Started",
			manual_status != "Not Started",
			job.get("transcript_json_file"),
			job.get("transcript_srt_file"),
			job.get("transcript_vtt_file"),
			job.get("manual_transcript_json_file"),
			job.get("manual_transcript_srt_file"),
			job.get("manual_transcript_vtt_file"),
			job.get("karaoke_ass_file"),
			job.get("karaoke_video_file"),
			job.get("karaoke_subtitle_json_file"),
			job.get("zip_file"),
		]
	)


def invalidate_downstream_assets(job, reason: str | None = None) -> None:
	reason = reason or DEFAULT_DOWNSTREAM_STALE_REASON
	job.transcription_status = "Not Started"
	job.karaoke_status = "Not Started"
	job.manual_transcript_status = "Not Started"
	job.transcription_error = None
	job.karaoke_error = None
	job.downstream_assets_stale = 1
	job.downstream_stale_reason = reason
	job.downstream_invalidated_at = now_datetime()
	job.zip_file = None
	job.save(ignore_permissions=True)


def clear_downstream_stale_after_transcription_complete(job) -> None:
	if cint(job.downstream_assets_stale):
		job.downstream_assets_stale = 0
		job.downstream_stale_reason = None
		job.save(ignore_permissions=True)


def has_current_transcription_assets(job) -> bool:
	if cint(job.get("downstream_assets_stale")):
		return False
	if (job.get("transcription_status") or "Not Started") != "Completed":
		return False
	return bool(job.get("transcript_json_file"))


def has_current_karaoke_assets(job) -> bool:
	if cint(job.get("downstream_assets_stale")):
		return False
	if (job.get("karaoke_status") or "Not Started") != "Completed":
		return False
	return bool(job.get("karaoke_ass_file") or job.get("karaoke_video_file"))


def downstream_assets_payload(job) -> dict:
	stale = bool(cint(job.get("downstream_assets_stale")))
	return {
		"downstream_assets_stale": stale,
		"downstream_stale_reason": job.get("downstream_stale_reason") if stale else None,
		"downstream_invalidated_at": job.get("downstream_invalidated_at"),
		"has_current_transcript_json": has_current_transcription_assets(job) and bool(job.get("transcript_json_file")),
		"has_current_transcript_srt": has_current_transcription_assets(job) and bool(job.get("transcript_srt_file")),
		"has_current_transcript_vtt": has_current_transcription_assets(job) and bool(job.get("transcript_vtt_file")),
		"has_current_manual_transcript": (
			not stale
			and (job.get("manual_transcript_status") or "Not Started") in ("Saved", "Approved")
			and bool(job.get("manual_transcript_json_file"))
		),
		"has_current_karaoke_ass": has_current_karaoke_assets(job) and bool(job.get("karaoke_ass_file")),
		"has_current_karaoke_video": has_current_karaoke_assets(job) and bool(job.get("karaoke_video_file")),
	}

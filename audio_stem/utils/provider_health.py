# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe.utils import add_to_date, cint, flt, now_datetime


def _status_counts(jobs, field: str) -> dict:
	counts = {"completed": 0, "failed": 0}
	for row in jobs:
		value = row.get(field) or "Not Started"
		if value == "Completed":
			counts["completed"] += 1
		elif value == "Failed":
			counts["failed"] += 1
	return counts


def _karaoke_ass_video_counts(jobs) -> dict:
	ass_completed = 0
	ass_failed = 0
	video_completed = 0
	video_failed = 0

	for row in jobs:
		status = row.get("karaoke_status") or "Not Started"
		has_ass = bool(row.get("karaoke_ass_file"))
		has_video = bool(row.get("karaoke_video_file"))
		error = (row.get("karaoke_error") or "").strip()

		if status == "Completed":
			if has_ass:
				ass_completed += 1
			if has_video:
				video_completed += 1
			elif error and "Video render" in error:
				video_failed += 1
		elif status == "Failed":
			if has_ass:
				video_failed += 1
			else:
				ass_failed += 1

	return {
		"ass_completed": ass_completed,
		"ass_failed": ass_failed,
		"video_completed": video_completed,
		"video_failed": video_failed,
	}


def _manual_correction_counts(jobs) -> dict:
	jobs_with_manual = 0
	approved_manual = 0
	ass_from_manual = 0
	manual_failures = 0

	for row in jobs:
		if row.get("manual_transcript_json_file"):
			jobs_with_manual += 1
		if (row.get("manual_transcript_status") or "") == "Approved":
			approved_manual += 1
		if row.get("karaoke_ass_file") and row.get("karaoke_source_transcript_file"):
			if row.karaoke_source_transcript_file == row.get("manual_transcript_json_file"):
				ass_from_manual += 1
		if (row.get("manual_transcript_status") or "") == "Saved" and row.get("transcription_status") == "Completed":
			if not row.get("manual_transcript_json_file"):
				manual_failures += 1

	return {
		"manual_correction_jobs_count": jobs_with_manual,
		"approved_manual_transcript_count": approved_manual,
		"karaoke_ass_from_manual_count": ass_from_manual,
		"manual_correction_failure_count": manual_failures,
	}


def get_provider_health_summary() -> dict:
	since = add_to_date(now_datetime(), hours=-24)
	jobs = frappe.get_all(
		"Audio Separation Job",
		filters={"modified": (">=", since)},
		fields=[
			"name",
			"status",
			"started_at",
			"completed_at",
			"duration_seconds",
			"transcription_status",
			"karaoke_status",
			"karaoke_ass_file",
			"karaoke_video_file",
			"karaoke_error",
			"karaoke_source_transcript_file",
			"manual_transcript_status",
			"manual_transcript_json_file",
			"transcript_json_file",
		],
		ignore_permissions=True,
	)

	completed = [row for row in jobs if row.status == "Completed"]
	failed = [row for row in jobs if row.status == "Failed"]
	terminal = completed + failed

	transcription = _status_counts(jobs, "transcription_status")
	karaoke = _status_counts(jobs, "karaoke_status")
	karaoke_detail = _karaoke_ass_video_counts(jobs)

	base_karaoke_fields = {
		"karaoke_completed_count": karaoke["completed"],
		"karaoke_failed_count": karaoke["failed"],
		"karaoke_ass_completed_count": karaoke_detail["ass_completed"],
		"karaoke_ass_failed_count": karaoke_detail["ass_failed"],
		"karaoke_video_completed_count": karaoke_detail["video_completed"],
		"karaoke_video_failed_count": karaoke_detail["video_failed"],
	}
	manual_correction = _manual_correction_counts(jobs)

	if not terminal:
		return {
			"status": "unknown",
			"success_rate": None,
			"completed_count": 0,
			"failed_count": 0,
			"average_processing_seconds": None,
			"last_success_at": None,
			"last_failure_at": None,
			"message": "No completed or failed separation jobs in the last 24 hours.",
			"transcription_completed_count": transcription["completed"],
			"transcription_failed_count": transcription["failed"],
			**base_karaoke_fields,
			**manual_correction,
		}

	success_rate = flt(len(completed)) / flt(len(terminal))
	durations = []
	for row in completed:
		if row.started_at and row.completed_at:
			delta = frappe.utils.get_datetime(row.completed_at) - frappe.utils.get_datetime(row.started_at)
			durations.append(max(delta.total_seconds(), 0))

	last_success = max((row.completed_at for row in completed if row.completed_at), default=None)
	last_failure = max((row.completed_at for row in failed if row.completed_at), default=None)

	status = "ok"
	message = "Provider outcomes look healthy."
	if success_rate < 0.5:
		status = "error"
		message = "High separation failure rate in the last 24 hours."
	elif success_rate < 0.8 or len(failed) >= 3:
		status = "warning"
		message = "Elevated separation failure rate in the last 24 hours."

	if transcription["failed"] >= 3:
		status = "warning" if status == "ok" else status
		message = f"{message} Transcription failures: {transcription['failed']}."
	if karaoke_detail["ass_failed"] >= 3:
		status = "warning" if status == "ok" else status
		message = f"{message} Karaoke ASS failures: {karaoke_detail['ass_failed']}."
	if karaoke_detail["video_failed"] >= 3:
		status = "warning" if status == "ok" else status
		message = f"{message} Karaoke video render failures: {karaoke_detail['video_failed']}."

	return {
		"status": status,
		"success_rate": round(success_rate, 3),
		"completed_count": len(completed),
		"failed_count": len(failed),
		"average_processing_seconds": round(sum(durations) / len(durations), 1) if durations else None,
		"last_success_at": last_success,
		"last_failure_at": last_failure,
		"message": message,
		"transcription_completed_count": transcription["completed"],
		"transcription_failed_count": transcription["failed"],
		**base_karaoke_fields,
		**manual_correction,
	}

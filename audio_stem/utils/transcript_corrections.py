# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import html
import json
import os
import re

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from audio_stem.utils.files import resolve_frappe_file_path
from audio_stem.utils.limits import get_settings
from audio_stem.utils.transcription_assets import (
	_attach_private_file,
	write_srt_from_segments_or_words,
	write_vtt_from_segments_or_words,
)

UNSAFE_TEXT_PATTERN = re.compile(
	r"(<\s*script|javascript\s*:|on\w+\s*=|<\s*/?\s*\w+[^>]*>)",
	re.IGNORECASE,
)
MANUAL_TRANSCRIPT_STATUSES = ("Not Started", "Draft", "Saved", "Approved")
KARAOKE_SOURCE_MODES = ("Auto", "Original Whisper", "Manual Corrected")
SEGMENT_WORD_TOLERANCE_SECONDS = 0.75


def _subtitle_settings():
	settings = get_settings()
	return {
		"max_words_per_line": cint(settings.subtitle_max_words_per_line)
		or cint(settings.karaoke_max_words_per_line)
		or 5,
		"max_line_duration": flt(settings.subtitle_max_line_duration_seconds) or 4.0,
		"min_word_duration": flt(settings.subtitle_min_word_duration_seconds) or 0.08,
		"snap_overlaps": bool(cint(settings.subtitle_snap_overlaps)),
	}


def sanitize_transcript_text(text: str | None) -> str:
	if text is None:
		return ""
	cleaned = html.unescape(str(text))
	if UNSAFE_TEXT_PATTERN.search(cleaned):
		frappe.throw(_("Transcript text contains unsafe HTML or script content."), frappe.ValidationError)
	cleaned = re.sub(r"<[^>]*>", "", cleaned)
	return cleaned.strip()


def _require_completed_transcription(job):
	if (job.transcription_status or "Not Started") != "Completed":
		frappe.throw(_("Completed transcription is required before editing."), frappe.ValidationError)
	if not job.transcript_json_file:
		frappe.throw(_("Original Whisper transcript is not available."), frappe.ValidationError)


def _load_json_file(file_url: str) -> dict:
	path = resolve_frappe_file_path(file_url)
	if not path or not os.path.exists(path):
		frappe.throw(_("Transcript file could not be loaded."), frappe.ValidationError)
	with open(path, encoding="utf-8") as handle:
		payload = json.load(handle)
	if not isinstance(payload, dict):
		frappe.throw(_("Transcript JSON must be an object."), frappe.ValidationError)
	return payload


def _normalize_word(word: dict, *, line_index: int | None = None) -> dict:
	text = sanitize_transcript_text(word.get("text") or word.get("word"))
	if not text:
		return {}
	start = flt(word.get("start"))
	end = flt(word.get("end"))
	entry = {
		"text": text,
		"start": start,
		"end": end,
	}
	if line_index is not None:
		entry["line"] = line_index
	if word.get("confidence") is not None:
		entry["confidence"] = flt(word.get("confidence"))
	if word.get("segment") is not None:
		entry["segment"] = cint(word.get("segment"))
	return entry


def _normalize_segment(segment: dict, *, segment_index: int) -> dict:
	text = sanitize_transcript_text(segment.get("text"))
	start = flt(segment.get("start"))
	end = flt(segment.get("end"))
	words = []
	for word in segment.get("words") or []:
		if not isinstance(word, dict):
			continue
		normalized = _normalize_word(word, line_index=segment_index)
		if normalized:
			words.append(normalized)
	if not text and words:
		text = " ".join(word["text"] for word in words)
	return {
		"id": segment.get("id", segment_index),
		"text": text,
		"start": start,
		"end": end,
		"words": words,
	}


def normalize_edit_payload(payload: dict) -> dict:
	if not isinstance(payload, dict):
		frappe.throw(_("Transcript payload must be an object."), frappe.ValidationError)

	text = sanitize_transcript_text(payload.get("text"))
	language = sanitize_transcript_text(payload.get("language")) or None
	duration = flt(payload.get("duration")) if payload.get("duration") is not None else None

	segments = []
	for index, segment in enumerate(payload.get("segments") or []):
		if not isinstance(segment, dict):
			continue
		normalized = _normalize_segment(segment, segment_index=index)
		if normalized.get("text") or normalized.get("words"):
			segments.append(normalized)

	words = []
	for index, word in enumerate(payload.get("words") or []):
		if not isinstance(word, dict):
			continue
		normalized = _normalize_word(word, line_index=cint(word.get("line") or index))
		if normalized:
			words.append(normalized)

	if not text:
		if segments:
			text = " ".join(segment["text"] for segment in segments if segment.get("text"))
		elif words:
			text = " ".join(word["text"] for word in words)

	return {
		"text": text,
		"language": language,
		"duration": duration,
		"segments": segments,
		"words": words,
	}


def _validate_timing_pair(start: float, end: float, *, label: str):
	if start < 0 or end < 0:
		frappe.throw(_("{0} timestamps must be non-negative.").format(label), frappe.ValidationError)
	if end <= start:
		frappe.throw(_("{0} end time must be greater than start time.").format(label), frappe.ValidationError)


def validate_transcript_edit_payload(payload: dict) -> None:
	normalized = normalize_edit_payload(payload)
	settings = _subtitle_settings()
	min_word_duration = settings["min_word_duration"]

	for segment_index, segment in enumerate(normalized.get("segments") or []):
		label = f"Segment {segment_index + 1}"
		_validate_timing_pair(flt(segment.get("start")), flt(segment.get("end")), label=label)
		seg_start = flt(segment.get("start"))
		seg_end = flt(segment.get("end"))
		for word_index, word in enumerate(segment.get("words") or []):
			word_label = f"{label}, word {word_index + 1}"
			word_start = flt(word.get("start"))
			word_end = flt(word.get("end"))
			_validate_timing_pair(word_start, word_end, label=word_label)
			if word_end - word_start < min_word_duration:
				frappe.throw(
					_("{0} is shorter than the minimum word duration.").format(word_label),
					frappe.ValidationError,
				)
			if word_start < seg_start - SEGMENT_WORD_TOLERANCE_SECONDS:
				frappe.throw(
					_("{0} starts before its segment.").format(word_label),
					frappe.ValidationError,
				)
			if word_end > seg_end + SEGMENT_WORD_TOLERANCE_SECONDS:
				frappe.throw(
					_("{0} ends after its segment.").format(word_label),
					frappe.ValidationError,
				)

	for word_index, word in enumerate(normalized.get("words") or []):
		label = f"Word {word_index + 1}"
		word_start = flt(word.get("start"))
		word_end = flt(word.get("end"))
		_validate_timing_pair(word_start, word_end, label=label)
		if word_end - word_start < min_word_duration:
			frappe.throw(
				_("{0} is shorter than the minimum word duration.").format(label),
				frappe.ValidationError,
			)


def snap_word_overlaps(words: list[dict], *, min_gap: float = 0.0) -> list[dict]:
	if not words:
		return []
	sorted_words = sorted(words, key=lambda item: (flt(item.get("start")), flt(item.get("end"))))
	result = []
	for word in sorted_words:
		entry = dict(word)
		if result:
			prev = result[-1]
			prev_end = flt(prev.get("end"))
			curr_start = flt(entry.get("start"))
			if curr_start < prev_end:
				entry["start"] = prev_end + min_gap
				if flt(entry.get("end")) <= flt(entry.get("start")):
					entry["end"] = flt(entry.get("start")) + _subtitle_settings()["min_word_duration"]
		result.append(entry)
	return result


def shift_timings(payload: dict, shift_seconds: float) -> dict:
	shift = flt(shift_seconds)
	normalized = normalize_edit_payload(payload)

	def _shift_value(value):
		return max(flt(value) + shift, 0.0)

	for segment in normalized.get("segments") or []:
		segment["start"] = _shift_value(segment.get("start"))
		segment["end"] = _shift_value(segment.get("end"))
		for word in segment.get("words") or []:
			word["start"] = _shift_value(word.get("start"))
			word["end"] = _shift_value(word.get("end"))

	for word in normalized.get("words") or []:
		word["start"] = _shift_value(word.get("start"))
		word["end"] = _shift_value(word.get("end"))

	validate_transcript_edit_payload(normalized)
	return normalized


def _text_from_words(words: list[dict]) -> str:
	parts = []
	for word in words:
		text = sanitize_transcript_text(word.get("text") or word.get("word"))
		if text:
			parts.append(text)
	return " ".join(parts).strip()


def _distribute_tokens_over_span(
	tokens: list[str],
	start: float,
	end: float,
	*,
	min_duration: float,
) -> list[dict]:
	if not tokens:
		return []

	start = flt(start)
	end = flt(end)
	if end <= start:
		end = start + max(min_duration * len(tokens), min_duration)

	duration = end - start
	step = duration / len(tokens)
	words = []
	for index, token in enumerate(tokens):
		word_start = start + index * step
		word_end = start + (index + 1) * step
		if word_end - word_start < min_duration:
			word_end = word_start + min_duration
		words.append({"text": token, "start": word_start, "end": word_end})
	return words


def sync_segment_text_and_words(segment: dict, *, min_word_duration: float = 0.08) -> dict:
	"""Keep segment words aligned with edited segment text for karaoke rendering."""
	segment = dict(segment or {})
	text = sanitize_transcript_text(segment.get("text") or "")
	words = [dict(word) for word in (segment.get("words") or []) if isinstance(word, dict)]
	words_text = _text_from_words(words)

	if not text and words_text:
		segment["text"] = words_text
		segment["words"] = words
		return segment

	if not text:
		segment["text"] = ""
		segment["words"] = []
		return segment

	if text == words_text:
		segment["text"] = text
		segment["words"] = words
		return segment

	tokens = text.split()
	if not tokens:
		segment["text"] = ""
		segment["words"] = []
		return segment

	if len(tokens) == len(words):
		synced_words = []
		for token, word in zip(tokens, words):
			entry = dict(word)
			entry["text"] = token
			entry.pop("word", None)
			synced_words.append(entry)
		segment["words"] = synced_words
		segment["text"] = text
		return segment

	segment["words"] = _distribute_tokens_over_span(
		tokens,
		flt(segment.get("start")),
		flt(segment.get("end")),
		min_duration=min_word_duration,
	)
	segment["text"] = text
	return segment


def _sync_transcript_words_and_text(payload: dict) -> dict:
	normalized = normalize_edit_payload(payload)
	settings = _subtitle_settings()
	min_word_duration = settings["min_word_duration"]

	synced_segments = [
		sync_segment_text_and_words(segment, min_word_duration=min_word_duration)
		for segment in normalized.get("segments") or []
	]
	normalized["segments"] = synced_segments

	if synced_segments:
		root_words = []
		for line_index, segment in enumerate(synced_segments):
			for word in segment.get("words") or []:
				entry = dict(word)
				entry["line"] = line_index
				root_words.append(entry)
		normalized["words"] = root_words
		normalized["text"] = " ".join(
			segment.get("text") or "" for segment in synced_segments if segment.get("text")
		).strip()
	elif normalized.get("words"):
		normalized["text"] = _text_from_words(normalized["words"])

	return normalized


def prepare_transcript_for_karaoke(payload: dict) -> dict:
	"""Normalize manual edits so karaoke uses corrected text, not stale word timings."""
	return apply_timing_normalization(payload)


def apply_timing_normalization(payload: dict) -> dict:
	normalized = _sync_transcript_words_and_text(payload)
	settings = _subtitle_settings()
	min_duration = settings["min_word_duration"]

	def _normalize_words(words: list[dict]) -> list[dict]:
		adjusted = []
		for word in words:
			entry = dict(word)
			start = flt(entry.get("start"))
			end = flt(entry.get("end"))
			if end - start < min_duration:
				end = start + min_duration
			entry["start"] = start
			entry["end"] = end
			adjusted.append(entry)
		if settings["snap_overlaps"]:
			return snap_word_overlaps(adjusted)
		return adjusted

	for segment in normalized.get("segments") or []:
		segment["words"] = _normalize_words(segment.get("words") or [])
		if segment.get("words"):
			segment["start"] = min(flt(word["start"]) for word in segment["words"])
			segment["end"] = max(flt(word["end"]) for word in segment["words"])
			segment["text"] = " ".join(word["text"] for word in segment["words"])

	normalized["words"] = _normalize_words(normalized.get("words") or [])
	return normalized


def rebuild_segment_text_from_words(segment: dict) -> dict:
	segment = dict(segment or {})
	words = segment.get("words") or []
	if words:
		segment["text"] = " ".join(word.get("text") or "" for word in words).strip()
	return segment


def load_transcript_for_edit(job) -> dict:
	_require_completed_transcription(job)
	source = "whisper"
	if job.manual_transcript_json_file:
		data = _load_json_file(job.manual_transcript_json_file)
		source = "manual"
	else:
		data = _load_json_file(job.transcript_json_file)

	normalized = normalize_edit_payload(data)
	return {
		"source": source,
		"manual_transcript_status": job.manual_transcript_status or "Not Started",
		"transcript": normalized,
	}


def _write_manual_json(job, payload: dict) -> str:
	content = json.dumps(payload, indent=2)
	file_url = _attach_private_file(
		job,
		file_name=f"{job.name}-manual-transcript.json",
		content=content,
		fieldname="manual_transcript_json_file",
	)
	job.manual_transcript_json_file = file_url
	return file_url


def generate_srt_from_manual_transcript(job) -> str:
	if not job.manual_transcript_json_file:
		frappe.throw(_("Manual transcript JSON is not available."), frappe.ValidationError)
	data = _load_json_file(job.manual_transcript_json_file)
	return write_srt_from_segments_or_words(
		job,
		data,
		fieldname="manual_transcript_srt_file",
		file_name=f"{job.name}-manual-transcript.srt",
	)


def generate_vtt_from_manual_transcript(job) -> str:
	if not job.manual_transcript_json_file:
		frappe.throw(_("Manual transcript JSON is not available."), frappe.ValidationError)
	data = _load_json_file(job.manual_transcript_json_file)
	return write_vtt_from_segments_or_words(
		job,
		data,
		fieldname="manual_transcript_vtt_file",
		file_name=f"{job.name}-manual-transcript.vtt",
	)


def _regenerate_manual_assets(job):
	generate_srt_from_manual_transcript(job)
	generate_vtt_from_manual_transcript(job)


def save_manual_transcript(job, payload, status: str = "Saved") -> dict:
	_require_completed_transcription(job)
	if status not in ("Draft", "Saved", "Approved"):
		frappe.throw(_("Invalid manual transcript status."), frappe.ValidationError)

	normalized = apply_timing_normalization(payload)
	validate_transcript_edit_payload(normalized)

	job.manual_transcript_text = normalized.get("text")
	_write_manual_json(job, normalized)
	_regenerate_manual_assets(job)
	job.manual_transcript_status = status
	job.manual_transcript_updated_at = now_datetime()
	job.manual_transcript_updated_by = frappe.session.user
	if status != "Approved":
		job.manual_transcript_approved_at = None
		job.manual_transcript_approved_by = None
	job.save(ignore_permissions=True)
	return load_transcript_for_edit(job)


def approve_manual_transcript(job) -> dict:
	if not job.manual_transcript_json_file:
		frappe.throw(_("Save manual corrections before approving."), frappe.ValidationError)
	validate_transcript_edit_payload(_load_json_file(job.manual_transcript_json_file))
	job.manual_transcript_status = "Approved"
	job.manual_transcript_approved_at = now_datetime()
	job.manual_transcript_approved_by = frappe.session.user
	job.save(ignore_permissions=True)
	return load_transcript_for_edit(job)


def reset_manual_transcript(job) -> dict:
	original_whisper = {
		"json": job.transcript_json_file,
		"srt": job.transcript_srt_file,
		"vtt": job.transcript_vtt_file,
		"text": job.transcript_text,
	}
	job.manual_transcript_status = "Not Started"
	job.manual_transcript_text = None
	job.manual_transcript_json_file = None
	job.manual_transcript_srt_file = None
	job.manual_transcript_vtt_file = None
	job.manual_transcript_updated_at = None
	job.manual_transcript_updated_by = None
	job.manual_transcript_approved_at = None
	job.manual_transcript_approved_by = None
	job.save(ignore_permissions=True)

	if original_whisper["json"] != job.transcript_json_file:
		frappe.throw(_("Original Whisper transcript was modified unexpectedly."), frappe.ValidationError)
	return {
		"manual_transcript_status": job.manual_transcript_status,
		"has_manual_transcript": False,
		"original_preserved": True,
	}


def _has_manual_transcript(job) -> bool:
	return bool(job.manual_transcript_json_file)


def _manual_is_approved(job) -> bool:
	return (job.manual_transcript_status or "Not Started") == "Approved"


def resolve_karaoke_source_mode(job) -> str:
	mode = (job.karaoke_source_mode or "Auto").strip()
	if mode not in KARAOKE_SOURCE_MODES:
		mode = "Auto"
	return mode


def resolve_karaoke_transcript_source(job) -> str:
	"""Return filesystem path to transcript JSON for karaoke rendering."""
	mode = resolve_karaoke_source_mode(job)
	use_manual = bool(cint(job.karaoke_use_manual_transcript))

	if mode == "Original Whisper":
		if not job.transcript_json_file:
			frappe.throw(_("Original Whisper transcript is not available."), frappe.ValidationError)
		path = resolve_frappe_file_path(job.transcript_json_file)
		if not path or not os.path.exists(path):
			frappe.throw(_("Could not resolve original Whisper transcript."), frappe.ValidationError)
		return path

	if mode == "Manual Corrected":
		if not _has_manual_transcript(job):
			frappe.throw(_("Manual corrected transcript is required but not available."), frappe.ValidationError)
		path = resolve_frappe_file_path(job.manual_transcript_json_file)
		if not path or not os.path.exists(path):
			frappe.throw(_("Could not resolve manual corrected transcript."), frappe.ValidationError)
		return path

	# Auto
	if use_manual and _manual_is_approved(job) and job.manual_transcript_json_file:
		path = resolve_frappe_file_path(job.manual_transcript_json_file)
		if path and os.path.exists(path):
			return path

	if not job.transcript_json_file:
		frappe.throw(_("Transcript JSON is required for karaoke subtitle generation."), frappe.ValidationError)
	path = resolve_frappe_file_path(job.transcript_json_file)
	if not path or not os.path.exists(path):
		frappe.throw(_("Could not resolve transcript JSON for karaoke."), frappe.ValidationError)
	return path


def resolve_karaoke_rendered_transcript_label(job) -> str | None:
	"""Return the transcript source used for the current karaoke output, if known."""
	source_file = job.get("karaoke_source_transcript_file")
	if not source_file:
		return None
	if job.manual_transcript_json_file and source_file == job.manual_transcript_json_file:
		return "Manual Corrected"
	if job.transcript_json_file and source_file == job.transcript_json_file:
		return "Original Whisper"
	return None


def resolve_karaoke_transcript_label(job) -> str:
	mode = resolve_karaoke_source_mode(job)
	use_manual = bool(cint(job.karaoke_use_manual_transcript))
	if mode == "Original Whisper":
		return "Original Whisper"
	if mode == "Manual Corrected":
		return "Manual Corrected"
	if use_manual and _manual_is_approved(job) and _has_manual_transcript(job):
		return "Manual Corrected"
	return "Original Whisper"


def resolve_karaoke_transcript_file_url(job) -> str:
	mode = resolve_karaoke_source_mode(job)
	use_manual = bool(cint(job.karaoke_use_manual_transcript))
	if mode == "Original Whisper":
		return job.transcript_json_file
	if mode == "Manual Corrected":
		return job.manual_transcript_json_file
	if use_manual and _manual_is_approved(job) and job.manual_transcript_json_file:
		return job.manual_transcript_json_file
	return job.transcript_json_file


def load_karaoke_transcript_data(job) -> dict:
	path = resolve_karaoke_transcript_source(job)
	with open(path, encoding="utf-8") as handle:
		payload = json.load(handle)
	return prepare_transcript_for_karaoke(payload)


def load_transcript_json_from_file_url(file_url: str) -> dict:
	return _load_json_file(file_url)

# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Transcription quality helpers: language/prompt resolution, chunk merge, diagnostics."""

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.limits import get_settings

WHISPER_PROMPT_MAX_CHARS = 800
# Legacy instruction-style default that Whisper may echo into the transcript output.
LEGACY_INSTRUCTION_PROMPT_MARKERS = (
	"Preserve repeated lines, chorus lines, Malay and English words",
	"Transcribe only the sung lyrics",
	"karaoke lyric transcription",
)
SECRET_PROMPT_PATTERNS = (
	re.compile(r"sk-[a-z0-9]{8,}", re.IGNORECASE),
	re.compile(r"api[_ -]?key", re.IGNORECASE),
)
ISO6391_LANGUAGE = re.compile(r"^[a-z]{2}$")
LANGUAGE_ALIASES = {
	"javanese": "jv",
	"malay": "ms",
	"english": "en",
	"indonesian": "id",
}
UNRELIABLE_TRANSCRIPT_WARNING = _(
	"Transcript may be incomplete or unreliable. Try setting the language, using Vocal source, or correcting lyrics manually."
)
ZERO_DURATION_TOLERANCE_SECONDS = 0.05
START_GAP_ABSOLUTE_SECONDS = 20.0
START_GAP_RATIO = 0.1


def validate_transcription_prompt_text(prompt: str | None) -> None:
	text = (prompt or "").strip()
	if not text:
		return
	for pattern in SECRET_PROMPT_PATTERNS:
		if pattern.search(text):
			frappe.throw(_("Transcription prompt must not contain secrets."), frappe.ValidationError)


def resolve_default_transcription_source(settings=None) -> str:
	settings = settings or get_settings()
	if cint(settings.transcription_use_vocal_stem_by_default):
		return "Vocal"
	return "Original"


def resolve_transcription_language(language: str | None = None, settings=None) -> str | None:
	settings = settings or get_settings()
	forced = (settings.transcription_force_language or "").strip().lower()
	if forced:
		if not ISO6391_LANGUAGE.match(forced):
			frappe.throw(
				_("Transcription force language must be ISO-639-1, for example ms or en."),
				frappe.ValidationError,
			)
		return forced

	language = (language or settings.default_transcription_language or "").strip().lower()
	if not language:
		return None
	if not ISO6391_LANGUAGE.match(language):
		frappe.throw(
			_("Transcription language must be ISO-639-1, for example ms or en."),
			frappe.ValidationError,
		)
	return language


def build_whisper_style_primer(settings=None, *, user_primer: str | None = None) -> str | None:
	"""Return a short Whisper style primer.

	Whisper treats `prompt` as prior transcript text, not ChatGPT-style instructions.
	Keep this empty or use a short lyric/spelling sample only.
	"""
	settings = settings or get_settings()
	if not cint(settings.transcription_prompt_enabled):
		return None

	primer = (user_primer if user_primer is not None else settings.transcription_prompt_text or "").strip()
	if not primer:
		return None

	validate_transcription_prompt_text(primer)
	_reject_instruction_style_prompt(primer)
	return primer[:WHISPER_PROMPT_MAX_CHARS]


def build_chunk_continuation_prompt(
	*,
	previous_chunk_text: str | None = None,
	style_primer: str | None = None,
) -> str | None:
	"""Build Whisper prompt for chunked transcription."""
	parts: list[str] = []
	if style_primer:
		parts.append(style_primer.strip())
	previous = (previous_chunk_text or "").strip()
	if previous:
		parts.append(previous[-WHISPER_PROMPT_MAX_CHARS:])
	combined = "\n".join(parts).strip()
	return combined[:WHISPER_PROMPT_MAX_CHARS] if combined else None


def build_transcription_prompt(
	settings=None,
	*,
	user_prompt: str | None = None,
	previous_chunk_text: str | None = None,
) -> str | None:
	settings = settings or get_settings()
	if previous_chunk_text:
		return build_chunk_continuation_prompt(
			previous_chunk_text=previous_chunk_text,
			style_primer=build_whisper_style_primer(settings, user_primer=user_prompt),
		)
	return build_whisper_style_primer(settings, user_primer=user_prompt)


def _reject_instruction_style_prompt(prompt: str) -> None:
	lowered = prompt.lower()
	for marker in LEGACY_INSTRUCTION_PROMPT_MARKERS:
		if marker.lower() in lowered:
			frappe.throw(
				_(
					"Transcription prompt must be a short lyric/style sample for Whisper, not instructions. Leave it blank or use a few words of example lyrics."
				),
				frappe.ValidationError,
			)


def detect_prompt_echo(transcript_text: str | None, settings=None) -> bool:
	text = (transcript_text or "").strip()
	if not text:
		return False

	settings = settings or get_settings()
	candidates = [settings.transcription_prompt_text or ""]
	candidates.extend(LEGACY_INSTRUCTION_PROMPT_MARKERS)

	for candidate in candidates:
		phrase = (candidate or "").strip()
		if len(phrase) < 20:
			continue
		lowered_text = text.lower()
		lowered_phrase = phrase.lower()
		if lowered_text.count(lowered_phrase) >= 2:
			return True
		if len(text) >= 80 and lowered_phrase in lowered_text and len(text) < len(lowered_phrase) * 3:
			return True
	return False


def count_transcript_words(transcript_data: dict) -> int:
	words = transcript_data.get("words") or []
	if words:
		return len([word for word in words if (word.get("word") or "").strip()])
	text = (transcript_data.get("text") or "").strip()
	if not text:
		return 0
	return len(re.findall(r"\S+", text))


def normalize_detected_language(language: str | None) -> str | None:
	if not language:
		return None
	normalized = language.strip().lower()
	return LANGUAGE_ALIASES.get(normalized, normalized)


def get_first_segment_start(segments: list[dict]) -> float | None:
	starts = [flt(segment.get("start")) for segment in segments if segment.get("start") is not None]
	if not starts:
		return None
	return min(starts)


def count_bad_word_timestamps(words: list[dict]) -> int:
	bad_count = 0
	for word in words:
		start = flt(word.get("start"))
		end = flt(word.get("end"))
		if end <= start or (end - start) < ZERO_DURATION_TOLERANCE_SECONDS:
			bad_count += 1
	return bad_count


def has_suspicious_start_gap(duration_seconds: float, first_segment_start: float | None) -> bool:
	if first_segment_start is None or duration_seconds < 60:
		return False
	if first_segment_start >= START_GAP_ABSOLUTE_SECONDS:
		return True
	if duration_seconds >= 120 and first_segment_start >= duration_seconds * START_GAP_RATIO:
		return True
	return False


def has_requested_language_mismatch(
	requested_language: str | None,
	detected_language: str | None,
) -> bool:
	requested = normalize_detected_language(requested_language)
	detected = normalize_detected_language(detected_language)
	if not requested or not detected:
		return False
	return requested != detected


def compute_transcription_quality_diagnostics(
	transcript_data: dict,
	*,
	duration_seconds: float | None = None,
	requested_language: str | None = None,
) -> dict:
	words = transcript_data.get("words") or []
	segments = transcript_data.get("segments") or []
	word_count = count_transcript_words(transcript_data)
	segment_count = len(segments)
	duration = flt(transcript_data.get("duration") or duration_seconds or 0)
	average_words_per_minute = round((word_count / duration) * 60, 2) if duration > 0 else 0
	has_word_timestamps = bool(words)
	detected_language = normalize_detected_language(transcript_data.get("language"))
	first_segment_start = get_first_segment_start(segments)
	bad_timestamp_count = count_bad_word_timestamps(words)

	low_confidence_warning = False
	for segment in segments:
		avg_logprob = segment.get("avg_logprob")
		if avg_logprob is not None and flt(avg_logprob) < -1.0:
			low_confidence_warning = True
			break

	suspiciously_short = False
	if duration >= 60:
		expected_words = max(12, int(duration / 60 * 8))
		if word_count < expected_words:
			suspiciously_short = True

	suspicious_start_gap = has_suspicious_start_gap(duration, first_segment_start)
	language_mismatch = has_requested_language_mismatch(requested_language, detected_language)
	has_bad_timestamps = bad_timestamp_count > 0
	prompt_echo_detected = detect_prompt_echo(transcript_data.get("text"))

	quality_unreliable = any(
		[
			suspiciously_short,
			suspicious_start_gap,
			has_bad_timestamps,
			low_confidence_warning,
			language_mismatch,
			prompt_echo_detected,
		]
	)

	return {
		"transcript_word_count": word_count,
		"transcript_segment_count": segment_count,
		"average_words_per_minute": average_words_per_minute,
		"detected_language": detected_language,
		"transcription_detected_language": detected_language,
		"first_segment_start": first_segment_start,
		"transcription_first_segment_start": first_segment_start,
		"bad_timestamp_count": bad_timestamp_count,
		"transcription_bad_timestamp_count": bad_timestamp_count,
		"has_word_timestamps": has_word_timestamps,
		"low_confidence_warning": low_confidence_warning,
		"suspiciously_short_transcript": suspiciously_short,
		"suspicious_start_gap": suspicious_start_gap,
		"language_mismatch": language_mismatch,
		"prompt_echo_detected": prompt_echo_detected,
		"transcription_quality_unreliable": quality_unreliable,
		"transcription_quality_warning": UNRELIABLE_TRANSCRIPT_WARNING if quality_unreliable else None,
	}


def apply_transcription_quality_diagnostics(
	job,
	transcript_data: dict,
	*,
	requested_language: str | None = None,
) -> dict:
	diagnostics = compute_transcription_quality_diagnostics(
		transcript_data,
		duration_seconds=flt(job.duration_seconds),
		requested_language=requested_language or job.transcription_language,
	)
	job.transcription_word_count = diagnostics["transcript_word_count"]
	job.transcription_segment_count = diagnostics["transcript_segment_count"]
	job.transcription_detected_language = diagnostics["transcription_detected_language"]
	job.transcription_first_segment_start = diagnostics["transcription_first_segment_start"]
	job.transcription_bad_timestamp_count = diagnostics["transcription_bad_timestamp_count"]
	job.transcription_quality_warning = diagnostics["transcription_quality_warning"]
	return diagnostics


def offset_transcript_timestamps(transcript_data: dict, offset_seconds: float) -> dict:
	offset = flt(offset_seconds)
	segments = []
	for segment in transcript_data.get("segments") or []:
		segments.append(
			{
				**segment,
				"start": flt(segment.get("start")) + offset,
				"end": flt(segment.get("end")) + offset,
			}
		)

	words = []
	for word in transcript_data.get("words") or []:
		words.append(
			{
				**word,
				"start": flt(word.get("start")) + offset,
				"end": flt(word.get("end")) + offset,
			}
		)

	return {
		**transcript_data,
		"segments": segments,
		"words": words,
	}


def merge_chunk_transcripts(chunk_results: list[dict]) -> dict:
	if not chunk_results:
		frappe.throw(_("Chunk transcription returned no results."), frappe.ValidationError)

	merged_segments: list[dict] = []
	merged_words: list[dict] = []
	text_parts: list[str] = []
	total_duration = 0.0
	language = None

	for chunk in chunk_results:
		text = (chunk.get("text") or "").strip()
		if text:
			text_parts.append(text)
		merged_segments.extend(chunk.get("segments") or [])
		merged_words.extend(chunk.get("words") or [])
		total_duration = max(total_duration, flt(chunk.get("duration")))
		language = language or chunk.get("language")

	merged_segments.sort(key=lambda item: (flt(item.get("start")), flt(item.get("end"))))
	merged_words.sort(key=lambda item: (flt(item.get("start")), flt(item.get("end"))))

	deduped_words: list[dict] = []
	seen_word_keys: set[tuple] = set()
	for word in merged_words:
		key = (
			round(flt(word.get("start")), 2),
			round(flt(word.get("end")), 2),
			(word.get("word") or "").strip().lower(),
		)
		if key in seen_word_keys:
			continue
		seen_word_keys.add(key)
		deduped_words.append(word)

	return {
		"text": " ".join(text_parts).strip(),
		"language": language,
		"duration": total_duration,
		"segments": merged_segments,
		"words": deduped_words,
	}

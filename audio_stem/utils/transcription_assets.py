# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
import os
import tempfile

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.ffmpeg_media import (
	get_file_size_mb,
	is_ffmpeg_available,
	preprocess_audio_for_transcription,
	transcode_audio_mono_mp3,
)
from audio_stem.utils.files import is_external_file_url, resolve_frappe_file_path
from audio_stem.utils.limits import get_settings


def estimate_transcription_cost(duration_seconds) -> float:
	settings = get_settings()
	minutes = flt(duration_seconds) / 60.0
	return flt(minutes * flt(settings.transcription_cost_per_minute_usd or 0))


def _download_external_audio(url: str) -> str:
	response = requests.get(url, timeout=120)
	response.raise_for_status()
	suffix = ".mp3"
	tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
	tmp.write(response.content)
	tmp.close()
	return tmp.name


def resolve_transcription_source_path(job, source: str) -> str:
	source = (source or "Vocal").strip()
	if source not in ("Vocal", "Original"):
		frappe.throw(_("Invalid transcription source."), frappe.ValidationError)

	if source == "Vocal":
		if job.status != "Completed":
			frappe.throw(_("Vocal transcription requires a completed separation job."), frappe.ValidationError)
		if job.vocal_file:
			path = resolve_frappe_file_path(job.vocal_file)
			if path:
				return path
		if job.vocal_output_url:
			return _download_external_audio(job.vocal_output_url)
		frappe.throw(_("Vocal output is not available for transcription."), frappe.ValidationError)

	if not job.original_file:
		frappe.throw(_("Original audio file is not available."), frappe.ValidationError)
	path = resolve_frappe_file_path(job.original_file)
	if path:
		return path
	if is_external_file_url(job.original_file):
		return _download_external_audio(job.original_file)
	frappe.throw(_("Could not resolve the audio file for transcription."), frappe.ValidationError)


def prepare_audio_for_whisper(local_audio_path: str) -> tuple[str, bool]:
	settings = get_settings()
	max_mb = cint(settings.transcription_max_file_size_mb) or 25
	size_mb = get_file_size_mb(local_audio_path)
	preprocess_enabled = bool(cint(settings.transcription_audio_preprocess_enabled))
	needs_transcode = size_mb > max_mb

	if not preprocess_enabled and not needs_transcode:
		return local_audio_path, False

	if not is_ffmpeg_available():
		if needs_transcode:
			frappe.log_error(
				title="Transcription preprocess unavailable",
				message=(
					f"ffmpeg is required to compress audio over {max_mb} MB for Whisper, "
					f"but it was not found on PATH. Using original file ({size_mb:.1f} MB)."
				),
			)
		return local_audio_path, False

	sample_rate = cint(settings.transcription_preprocess_sample_rate) or 16000
	channels = cint(settings.transcription_preprocess_channels) or 1
	bitrate = (settings.transcription_preprocess_bitrate or "64k").strip() or "64k"

	try:
		if preprocess_enabled:
			output_path = preprocess_audio_for_transcription(
				local_audio_path,
				sample_rate=sample_rate,
				channels=channels,
				bitrate=bitrate,
				normalize_volume=True,
			)
			return output_path, True

		return transcode_audio_mono_mp3(local_audio_path, bitrate=bitrate), True
	except Exception:
		frappe.log_error(
			title="Transcription preprocess failed",
			message=frappe.get_traceback(),
		)
		return local_audio_path, False


def _attach_private_file(job, *, file_name: str, content: bytes | str, fieldname: str | None = None) -> str:
	if isinstance(content, str):
		content = content.encode("utf-8")
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": file_name,
			"attached_to_doctype": job.doctype,
			"attached_to_name": job.name,
			"attached_to_field": fieldname,
			"is_private": 1,
			"content": content,
		}
	)
	file_doc.save(ignore_permissions=True)
	return file_doc.file_url


def write_transcript_json(job, transcript_data: dict) -> str:
	payload = {
		"text": transcript_data.get("text"),
		"language": transcript_data.get("language"),
		"duration": transcript_data.get("duration"),
		"segments": transcript_data.get("segments") or [],
		"words": transcript_data.get("words") or [],
	}
	content = json.dumps(payload, indent=2)
	file_url = _attach_private_file(
		job,
		file_name=f"{job.name}-transcript.json",
		content=content,
		fieldname="transcript_json_file",
	)
	job.transcript_json_file = file_url
	return file_url


def _format_srt_timestamp(seconds: float) -> str:
	ms = int(round(max(flt(seconds), 0) * 1000))
	hours, rem = divmod(ms, 3600000)
	minutes, rem = divmod(rem, 60000)
	secs, millis = divmod(rem, 1000)
	return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _format_vtt_timestamp(seconds: float) -> str:
	ms = int(round(max(flt(seconds), 0) * 1000))
	hours, rem = divmod(ms, 3600000)
	minutes, rem = divmod(rem, 60000)
	secs, millis = divmod(rem, 1000)
	return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


def write_srt_from_segments_or_words(
	job,
	transcript_data: dict,
	*,
	fieldname: str = "transcript_srt_file",
	file_name: str | None = None,
) -> str:
	lines = []
	words = transcript_data.get("words") or []
	segments = transcript_data.get("segments") or []

	if words:
		idx = 1
		for word in words:
			text = (word.get("word") or "").strip()
			if not text:
				continue
			lines.append(str(idx))
			lines.append(
				f"{_format_srt_timestamp(word.get('start', 0))} --> {_format_srt_timestamp(word.get('end', 0))}"
			)
			lines.append(text)
			lines.append("")
			idx += 1
	elif segments:
		for idx, segment in enumerate(segments, start=1):
			text = (segment.get("text") or "").strip()
			if not text:
				continue
			lines.append(str(idx))
			lines.append(
				f"{_format_srt_timestamp(segment.get('start', 0))} --> {_format_srt_timestamp(segment.get('end', 0))}"
			)
			lines.append(text)
			lines.append("")
	else:
		text = (transcript_data.get("text") or "").strip()
		if text:
			lines.extend(["1", "00:00:00,000 --> 00:00:05,000", text, ""])

	content = "\n".join(lines).strip() + "\n"
	file_url = _attach_private_file(
		job,
		file_name=file_name or f"{job.name}-transcript.srt",
		content=content,
		fieldname=fieldname,
	)
	job.set(fieldname, file_url)
	return file_url


def write_vtt_from_segments_or_words(
	job,
	transcript_data: dict,
	*,
	fieldname: str = "transcript_vtt_file",
	file_name: str | None = None,
) -> str:
	lines = ["WEBVTT", ""]
	words = transcript_data.get("words") or []
	segments = transcript_data.get("segments") or []

	if words:
		for word in words:
			text = (word.get("word") or "").strip()
			if not text:
				continue
			lines.append(
				f"{_format_vtt_timestamp(word.get('start', 0))} --> {_format_vtt_timestamp(word.get('end', 0))}"
			)
			lines.append(text)
			lines.append("")
	elif segments:
		for segment in segments:
			text = (segment.get("text") or "").strip()
			if not text:
				continue
			lines.append(
				f"{_format_vtt_timestamp(segment.get('start', 0))} --> {_format_vtt_timestamp(segment.get('end', 0))}"
			)
			lines.append(text)
			lines.append("")
	else:
		text = (transcript_data.get("text") or "").strip()
		if text:
			lines.extend(["00:00:00.000 --> 00:00:05.000", text, ""])

	content = "\n".join(lines).strip() + "\n"
	file_url = _attach_private_file(
		job,
		file_name=file_name or f"{job.name}-transcript.vtt",
		content=content,
		fieldname=fieldname,
	)
	job.set(fieldname, file_url)
	return file_url


def cleanup_temp_path(path: str | None, *, should_cleanup: bool):
	if should_cleanup and path and os.path.exists(path):
		try:
			os.unlink(path)
		except OSError:
			pass

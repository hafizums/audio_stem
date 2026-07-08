# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Read-only helpers to inspect which audio file whisper-1 would receive."""

from __future__ import annotations

import os

import frappe
from frappe import _
from frappe.utils import cint

from audio_stem.utils.ffmpeg_media import get_file_size_mb, probe_audio_stream_info
from audio_stem.utils.files import is_external_file_url, resolve_frappe_file_path
from audio_stem.utils.limits import get_settings
from audio_stem.utils.transcription_assets import (
	cleanup_temp_path,
	resolve_transcription_source_path,
)

WHISPER_TRANSCODE_PROFILE = {
	"codec": "mp3",
	"channels": 1,
	"sample_rate": 16000,
	"bitrate_kbps": 64,
}
TRANSCODE_TARGET_LABEL = "mono_mp3_16khz_64k"
AS_IS_LABEL = "as_is"
EXTERNAL_KINDS = frozenset({"external_vocal_url", "external_original_url"})


def _basename_from_file_url(file_url: str | None) -> str | None:
	if not file_url:
		return None
	if file_url.startswith("/"):
		file_name = frappe.db.get_value("File", {"file_url": file_url}, "file_name")
		if file_name:
			return file_name
	return os.path.basename(file_url.split("?", 1)[0])


def classify_transcription_source(job, source: str) -> dict:
	source = (source or "Vocal").strip()
	if source not in ("Vocal", "Original"):
		frappe.throw(_("Invalid transcription source."), frappe.ValidationError)

	if source == "Vocal":
		if job.vocal_file:
			local_path = resolve_frappe_file_path(job.vocal_file)
			if local_path:
				return {
					"source_kind": "local_vocal_file",
					"source_file_url": job.vocal_file,
					"source_file_name": _basename_from_file_url(job.vocal_file),
				}
		if job.vocal_output_url:
			return {
				"source_kind": "external_vocal_url",
				"source_file_url": job.vocal_output_url,
				"source_file_name": os.path.basename(job.vocal_output_url.split("?", 1)[0]) or None,
			}
		frappe.throw(_("Vocal output is not available for transcription."), frappe.ValidationError)

	if job.original_file:
		local_path = resolve_frappe_file_path(job.original_file)
		if local_path:
			return {
				"source_kind": "local_original_file",
				"source_file_url": job.original_file,
				"source_file_name": _basename_from_file_url(job.original_file),
			}
		if is_external_file_url(job.original_file):
			return {
				"source_kind": "external_original_url",
				"source_file_url": job.original_file,
				"source_file_name": os.path.basename(job.original_file.split("?", 1)[0]) or None,
			}
	frappe.throw(_("Could not resolve the audio file for transcription."), frappe.ValidationError)


def build_whisper_input_report(job, source: str = "Vocal", *, probe_external: bool = False) -> dict:
	"""Describe the audio profile that would be sent to whisper-1 for a job."""
	settings = get_settings()
	source = (source or "Vocal").strip()
	classification = classify_transcription_source(job, source)

	max_mb = cint(settings.transcription_max_file_size_mb) or 25
	model = (settings.transcription_model or "whisper-1").strip() or "whisper-1"

	report = {
		"job_name": job.name,
		"transcription_source": source,
		"model": model,
		"max_file_size_mb": max_mb,
		**classification,
		"source_size_mb": None,
		"will_transcode_for_whisper": None,
		"whisper_input_profile": None,
		"whisper_input_audio": None,
		"notes": [],
	}

	if source == "Vocal" and job.status != "Completed":
		report["error"] = _("Vocal transcription requires a completed separation job.")
		return report

	source_kind = classification["source_kind"]
	if source_kind in EXTERNAL_KINDS and not probe_external:
		report["whisper_input_profile"] = "unknown_until_download"
		report["notes"].append(
			_("External source; set probe_external=1 to download and inspect the file that would be sent.")
		)
		return report

	local_path = None
	should_cleanup = source_kind in EXTERNAL_KINDS
	try:
		local_path = resolve_transcription_source_path(job, source)
	except Exception as exc:
		report["error"] = str(exc)
		return report

	try:
		size_mb = get_file_size_mb(local_path)
		preprocess_enabled = bool(cint(settings.transcription_audio_preprocess_enabled))
		will_preprocess = preprocess_enabled or size_mb > max_mb
		sample_rate = cint(settings.transcription_preprocess_sample_rate) or 16000
		channels = cint(settings.transcription_preprocess_channels) or 1
		bitrate = (settings.transcription_preprocess_bitrate or "64k").strip() or "64k"
		bitrate_kbps = int("".join(ch for ch in bitrate if ch.isdigit()) or 64)

		report["source_size_mb"] = round(size_mb, 3)
		report["will_transcode_for_whisper"] = will_preprocess
		report["preprocess_enabled"] = preprocess_enabled
		report["whisper_input_profile"] = TRANSCODE_TARGET_LABEL if will_preprocess else AS_IS_LABEL

		if will_preprocess:
			report["whisper_input_audio"] = {
				"codec": "mp3",
				"channels": channels,
				"sample_rate": sample_rate,
				"bitrate_kbps": bitrate_kbps,
			}
			if preprocess_enabled and size_mb <= max_mb:
				report["notes"].append(
					_("Preprocessing enabled; ffmpeg converts to mono {0} Hz MP3 @ {1} before {2}.").format(
						sample_rate, bitrate, model
					)
				)
			elif size_mb > max_mb:
				report["notes"].append(
					_(
						"Source is {0:.1f} MB (over {1} MB); ffmpeg transcodes to mono {2} Hz MP3 @ {3} before {4}."
					).format(size_mb, max_mb, sample_rate, bitrate, model)
				)
		else:
			stream_info = probe_audio_stream_info(local_path)
			if stream_info:
				report["whisper_input_audio"] = stream_info
			report["notes"].append(
				_("Source is {0:.1f} MB (within {1} MB); sent to {2} as-is.").format(size_mb, max_mb, model)
			)
	finally:
		cleanup_temp_path(local_path, should_cleanup=should_cleanup)

	return report

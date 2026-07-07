# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
import os
import tempfile

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.ffmpeg_media import create_color_video_with_audio, ensure_ffmpeg_available
from audio_stem.utils.files import is_external_file_url, resolve_frappe_file_path
from audio_stem.utils.limits import get_settings


def is_pycaps_available() -> bool:
	try:
		from pycaps import CapsPipelineBuilder  # noqa: F401
	except ImportError:
		return False
	return True


def build_karaoke_words_json(job, transcript_data: dict) -> dict:
	words = []
	segments = transcript_data.get("segments") or []
	raw_words = transcript_data.get("words") or []
	line_index = 0

	if raw_words:
		current_line = []
		for word in raw_words:
			text = (word.get("word") or "").strip()
			if not text:
				continue
			entry = {
				"text": text,
				"start": flt(word.get("start")),
				"end": flt(word.get("end")),
				"line": line_index,
			}
			words.append(entry)
			current_line.append(text)
			if text.endswith((".", "!", "?", ",")) or len(current_line) >= 8:
				line_index += 1
				current_line = []
	elif segments:
		for segment in segments:
			text = (segment.get("text") or "").strip()
			if not text:
				continue
			words.append(
				{
					"text": text,
					"start": flt(segment.get("start")),
					"end": flt(segment.get("end")),
					"line": line_index,
				}
			)
			line_index += 1
	else:
		text = (transcript_data.get("text") or "").strip()
		if text:
			words.append(
				{
					"text": text,
					"start": 0.0,
					"end": flt(transcript_data.get("duration") or job.duration_seconds or 5),
					"line": 0,
				}
			)

	lines = {}
	for word in words:
		lines.setdefault(word["line"], []).append(word)

	return {
		"job": job.name,
		"language": transcript_data.get("language"),
		"duration": flt(transcript_data.get("duration") or job.duration_seconds),
		"words": words,
		"lines": [{"line": line_no, "words": line_words} for line_no, line_words in sorted(lines.items())],
	}


def write_karaoke_json(job, karaoke_data: dict) -> str:
	content = json.dumps(karaoke_data, indent=2)
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{job.name}-karaoke.json",
			"attached_to_doctype": job.doctype,
			"attached_to_name": job.name,
			"attached_to_field": "karaoke_subtitle_json_file",
			"is_private": 1,
			"content": content.encode("utf-8"),
		}
	)
	file_doc.save(ignore_permissions=True)
	job.karaoke_subtitle_json_file = file_doc.file_url
	return file_doc.file_url


def _resolve_audio_path_for_video(job) -> str:
	settings = get_settings()
	use_instrumental = bool(cint(settings.karaoke_include_instrumental_audio))
	candidates = []
	if use_instrumental:
		if job.instrumental_file:
			candidates.append(resolve_frappe_file_path(job.instrumental_file))
		if job.instrumental_output_url:
			candidates.append(job.instrumental_output_url)
	if job.original_file:
		candidates.append(resolve_frappe_file_path(job.original_file) or job.original_file)
	if job.vocal_file:
		candidates.append(resolve_frappe_file_path(job.vocal_file))

	for candidate in candidates:
		if not candidate:
			continue
		if is_external_file_url(candidate):
			response = requests.get(candidate, timeout=120)
			response.raise_for_status()
			tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
			tmp.write(response.content)
			tmp.close()
			return tmp.name
		if os.path.exists(candidate):
			return candidate
	frappe.throw(_("No audio source is available for karaoke video generation."), frappe.ValidationError)


def create_plain_lyrics_video(job, background_color: str | None = None) -> str:
	ensure_ffmpeg_available()
	settings = get_settings()
	audio_path = _resolve_audio_path_for_video(job)
	duration = flt(job.duration_seconds or 30)
	width = cint(settings.karaoke_output_width) or 1080
	height = cint(settings.karaoke_output_height) or 1920
	color = background_color or settings.karaoke_background_color or "#111111"
	output_path = tempfile.mktemp(suffix=".mp4")
	create_color_video_with_audio(
		output_path=output_path,
		duration_seconds=duration,
		width=width,
		height=height,
		background_color=color,
		audio_path=audio_path,
	)
	return output_path


def render_karaoke_video_with_pycaps(
	job,
	input_video_path: str,
	karaoke_json_path: str | None,
	template: str | None = None,
) -> str:
	settings = get_settings()
	template = (template or job.karaoke_template or settings.karaoke_default_template or "hype").strip()
	output_path = tempfile.mktemp(suffix=".mp4")

	vtt_path = resolve_frappe_file_path(job.transcript_vtt_file) if job.transcript_vtt_file else None
	srt_path = resolve_frappe_file_path(job.transcript_srt_file) if job.transcript_srt_file else None
	transcript_path = vtt_path or srt_path

	try:
		from pycaps import CapsPipelineBuilder, TemplateLoader, TranscriptFormat
	except ImportError as exc:
		frappe.log_error(title="PyCaps import failed", message=frappe.get_traceback())
		if "CapsPipelineBuilder" in str(exc):
			frappe.throw(
				_(
					"Karaoke rendering requires the pycaps-ai subtitle package. "
					"Install it with: pip install \"pycaps-ai[base]\" && playwright install chromium"
				),
				frappe.ValidationError,
			)
		frappe.throw(_("Karaoke rendering is unavailable on this server."), frappe.ValidationError)

	def _transcript_format(path: str):
		return TranscriptFormat.VTT if path.endswith(".vtt") else TranscriptFormat.SRT

	def _karaoke_dict_to_whisper_json(karaoke_data: dict) -> dict:
		words = karaoke_data.get("words") or []
		if not words:
			return {"segments": []}
		return {
			"segments": [
				{
					"words": [
						{
							"word": (word.get("text") or word.get("word") or "").strip(),
							"start": flt(word.get("start")),
							"end": flt(word.get("end")),
						}
						for word in words
						if (word.get("text") or word.get("word") or "").strip()
					]
				}
			]
		}

	rendered = False
	if transcript_path:
		fmt = _transcript_format(transcript_path)
		try:
			pipeline = (
				TemplateLoader(template)
				.with_input_video(input_video_path)
				.load(False)
				.with_output_video(output_path)
				.with_transcription_file(transcript_path, fmt)
				.build()
			)
			pipeline.run()
			rendered = True
		except Exception:
			frappe.log_error(title=f"PyCaps TemplateLoader failed for {job.name}", message=frappe.get_traceback())

	if not rendered:
		builder = CapsPipelineBuilder().with_input_video(input_video_path).with_output_video(output_path)
		if transcript_path:
			fmt = _transcript_format(transcript_path)
			builder.with_transcription_file(transcript_path, fmt)
		elif karaoke_json_path and os.path.exists(karaoke_json_path):
			with open(karaoke_json_path, encoding="utf-8") as handle:
				karaoke_data = json.load(handle)
			builder.with_transcription(
				_karaoke_dict_to_whisper_json(karaoke_data),
				TranscriptFormat.WHISPER_JSON,
			)
		builder.build().run()

	if not os.path.exists(output_path):
		frappe.throw(_("Karaoke video rendering did not produce an output file."), frappe.ValidationError)

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{job.name}-karaoke.mp4",
			"attached_to_doctype": job.doctype,
			"attached_to_name": job.name,
			"attached_to_field": "karaoke_video_file",
			"is_private": 1,
			"content": open(output_path, "rb").read(),
		}
	)
	file_doc.save(ignore_permissions=True)
	try:
		os.unlink(output_path)
	except OSError:
		pass
	return file_doc.file_url

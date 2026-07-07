# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
import os
import sys
import tempfile

import frappe
import requests
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.ffmpeg_media import (
	create_color_video_with_audio,
	ensure_ffmpeg_available,
	is_ffprobe_available,
)
from audio_stem.utils.files import is_external_file_url, resolve_frappe_file_path
from audio_stem.utils.limits import get_settings

STYLE_PRESET_FACTORIES = {
	"default_1080p": "default_1080p",
	"default_720p": "default_720p",
	"mobile_1080x1920": "mobile_1080x1920",
	"hype": "mobile_1080x1920",
	"minimalist": "default_1080p",
	"classic": "default_1080p",
	"vibrant": "default_1080p",
	"default": "default_1080p",
}


def _karaoke_engine_bench_path() -> str:
	from frappe.utils import get_bench_path

	return os.path.join(get_bench_path(), "apps", "karaoke_engine")


def _ensure_karaoke_engine_on_path() -> None:
	karaoke_root = _karaoke_engine_bench_path()
	if os.path.isdir(karaoke_root) and karaoke_root not in sys.path:
		sys.path.insert(0, karaoke_root)


def is_karaoke_engine_available() -> bool:
	try:
		from karaoke_engine import KaraokeEngine  # noqa: F401

		return True
	except ImportError:
		pass

	try:
		_ensure_karaoke_engine_on_path()
		from karaoke_engine import KaraokeEngine  # noqa: F401

		return True
	except ImportError:
		frappe.log_error(
			title="karaoke_engine import failed",
			message=frappe.get_traceback(),
		)
		return False


def get_karaoke_engine_version() -> str:
	try:
		import karaoke_engine

		return getattr(karaoke_engine, "__version__", "unknown")
	except ImportError:
		return ""


def resolve_karaoke_style_preset(preset: str | None = None) -> str:
	settings = get_settings()
	preset = (preset or settings.karaoke_style_preset or "default_1080p").strip()
	return preset or "default_1080p"


def get_karaoke_style(preset: str | None = None):
	from karaoke_engine import KaraokeStyle

	resolved = resolve_karaoke_style_preset(preset).lower()
	factory_name = STYLE_PRESET_FACTORIES.get(resolved, "default_1080p")
	factory = getattr(KaraokeStyle, factory_name, KaraokeStyle.default_1080p)
	return factory()


def resolve_transcript_json_for_karaoke(job) -> str:
	if not job.transcript_json_file:
		frappe.throw(
			_("Transcript JSON is required for karaoke subtitle generation."),
			frappe.ValidationError,
		)
	path = resolve_frappe_file_path(job.transcript_json_file)
	if not path or not os.path.exists(path):
		frappe.throw(_("Could not resolve transcript JSON for karaoke."), frappe.ValidationError)
	return path


def resolve_karaoke_audio_path(job) -> str:
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


def create_background_video_for_karaoke(job, background_color: str | None = None) -> str:
	settings = get_settings()
	audio_path = resolve_karaoke_audio_path(job)
	duration = flt(job.duration_seconds or 30)
	width = cint(settings.karaoke_video_width) or 1080
	height = cint(settings.karaoke_video_height) or 1920
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


def build_karaoke_words_json(job, transcript_data: dict) -> dict:
	words = []
	segments = transcript_data.get("segments") or []
	raw_words = transcript_data.get("words") or []
	line_index = 0

	if raw_words:
		current_line = []
		for word in raw_words:
			text = (word.get("word") or word.get("text") or "").strip()
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


def _attach_private_binary_file(job, *, file_name: str, content: bytes, fieldname: str) -> str:
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


def _segment_options_from_settings():
	from karaoke_engine import SegmentOptions

	settings = get_settings()
	return SegmentOptions(max_words_per_line=cint(settings.karaoke_max_words_per_line) or 5)


def _render_options_from_settings():
	from karaoke_engine import RenderOptions

	settings = get_settings()
	return RenderOptions(
		crf=cint(settings.karaoke_ffmpeg_crf) or 18,
		preset=(settings.karaoke_ffmpeg_preset or "veryfast").strip(),
	)


def _read_file_bytes(path: str) -> bytes:
	with open(path, "rb") as handle:
		return handle.read()


def build_karaoke_ass_with_engine(job, *, style_preset: str | None = None) -> str:
	if not is_karaoke_engine_available():
		frappe.throw(
			_("Karaoke subtitle generation requires karaoke_engine. Install it on the server."),
			frappe.ValidationError,
		)

	from karaoke_engine import KaraokeEngine

	settings = get_settings()
	transcript_path = resolve_transcript_json_for_karaoke(job)
	ass_path = tempfile.mktemp(suffix=".ass")
	style = get_karaoke_style(style_preset)
	width = cint(settings.karaoke_video_width) or 1080
	height = cint(settings.karaoke_video_height) or 1920

	engine = KaraokeEngine()
	engine.create_ass(
		transcript_path=transcript_path,
		output_path=ass_path,
		style=style,
		segment_options=_segment_options_from_settings(),
		play_res_x=width,
		play_res_y=height,
		title=f"Karaoke {job.name}",
	)

	job.karaoke_source_transcript_file = job.transcript_json_file
	file_url = _attach_private_binary_file(
		job,
		file_name=f"{job.name}-karaoke.ass",
		content=_read_file_bytes(ass_path),
		fieldname="karaoke_ass_file",
	)
	job.karaoke_ass_file = file_url
	job.karaoke_engine_version = get_karaoke_engine_version()

	try:
		os.unlink(ass_path)
	except OSError:
		pass

	return file_url


def render_karaoke_video_with_engine(job, *, style_preset: str | None = None, input_video_path: str | None = None) -> str:
	if not is_karaoke_engine_available():
		frappe.throw(
			_("Karaoke video rendering requires karaoke_engine. Install it on the server."),
			frappe.ValidationError,
		)
	ensure_ffmpeg_available()
	if not is_ffprobe_available():
		frappe.throw(
			_("ffprobe is required for karaoke video rendering but was not found on the server PATH."),
			frappe.ValidationError,
		)

	from karaoke_engine import KaraokeEngine

	settings = get_settings()
	transcript_path = resolve_transcript_json_for_karaoke(job)
	style = get_karaoke_style(style_preset)
	width = cint(settings.karaoke_video_width) or 1080
	height = cint(settings.karaoke_video_height) or 1920

	created_temp_video = False
	source_video_path = input_video_path
	if not source_video_path:
		source_video_path = create_background_video_for_karaoke(job)
		created_temp_video = True

	output_path = tempfile.mktemp(suffix=".mp4")
	ass_path = tempfile.mktemp(suffix=".ass")

	try:
		engine = KaraokeEngine()
		result = engine.render_video(
			video_path=source_video_path,
			transcript_path=transcript_path,
			output_path=output_path,
			ass_output_path=ass_path,
			style=style,
			segment_options=_segment_options_from_settings(),
			play_res_x=width,
			play_res_y=height,
			title=f"Karaoke {job.name}",
			render_options=_render_options_from_settings(),
			auto_probe_resolution=False,
		)

		file_url = _attach_private_binary_file(
			job,
			file_name=f"{job.name}-karaoke.mp4",
			content=_read_file_bytes(str(result.output_path)),
			fieldname="karaoke_video_file",
		)
		job.karaoke_video_file = file_url

		if not job.karaoke_ass_file:
			job.karaoke_ass_file = _attach_private_binary_file(
				job,
				file_name=f"{job.name}-karaoke.ass",
				content=_read_file_bytes(str(result.ass_path)),
				fieldname="karaoke_ass_file",
			)

		if created_temp_video and source_video_path:
			job.karaoke_render_source_video_file = _attach_private_binary_file(
				job,
				file_name=f"{job.name}-karaoke-source.mp4",
				content=_read_file_bytes(source_video_path),
				fieldname="karaoke_render_source_video_file",
			)

		return file_url
	finally:
		for path in (ass_path, output_path):
			try:
				if path and os.path.exists(path):
					os.unlink(path)
			except OSError:
				pass
		if created_temp_video and source_video_path and os.path.exists(source_video_path):
			try:
				os.unlink(source_video_path)
			except OSError:
				pass

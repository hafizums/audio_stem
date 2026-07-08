# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Karaoke MP4 background video resolution and FFmpeg preparation."""

from __future__ import annotations

import os
import tempfile
from mimetypes import guess_type

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.ffmpeg_media import (
	build_prepare_video_background_ffmpeg_args,
	ensure_ffmpeg_available,
	prepare_video_background_with_audio,
	probe_media_duration,
)
from audio_stem.utils.files import is_external_file_url, resolve_frappe_file_path
from audio_stem.utils.limits import get_settings

SOURCE_JOB_UPLOAD = "Job Upload"
SOURCE_SETTINGS_DEFAULT = "Settings Default"
SOURCE_GENERATED_COLOR = "Generated Color"

ALLOWED_VIDEO_EXTENSIONS = (".mp4", ".mov", ".webm", ".mkv", ".m4v")
ALLOWED_VIDEO_MIMETYPES = {
	"video/mp4",
	"video/webm",
	"video/quicktime",
	"video/x-matroska",
	"video/mkv",
}


def _is_system_manager() -> bool:
	return frappe.session.user == "Administrator" or "System Manager" in frappe.get_roles()


def can_upload_karaoke_background(job=None) -> bool:
	settings = get_settings()
	if _is_system_manager():
		return True
	return bool(cint(settings.allow_user_karaoke_background_upload))


def validate_background_video_file(file_doc) -> None:
	"""Validate uploaded/selected background video metadata."""
	if not file_doc:
		frappe.throw(_("Background video file was not found."), frappe.ValidationError)

	file_name = (file_doc.file_name or file_doc.name or "").lower()
	ext = os.path.splitext(file_name)[1]
	mime = (
		getattr(file_doc, "content_type", None)
		or guess_type(file_doc.file_name or "")[0]
		or ""
	).lower()

	if ext in ALLOWED_VIDEO_EXTENSIONS:
		return
	if mime in ALLOWED_VIDEO_MIMETYPES:
		return

	frappe.throw(
		_("Please upload a supported background video file (MP4, MOV, WEBM, MKV)."),
		frappe.ValidationError,
	)


def _resolve_local_video_path(file_url: str | None) -> str | None:
	if not file_url:
		return None
	if is_external_file_url(file_url):
		return file_url
	path = resolve_frappe_file_path(file_url)
	if path and os.path.exists(path):
		return path
	return None


def _safe_file_name(file_url: str | None) -> str | None:
	if not file_url:
		return None
	return frappe.db.get_value("File", {"file_url": file_url}, "file_name") or os.path.basename(file_url)


def resolve_karaoke_background_video(job) -> dict:
	"""Resolve karaoke background video for MP4 rendering."""
	settings = get_settings()
	result = {
		"source": SOURCE_GENERATED_COLOR,
		"local_path": None,
		"file_url": None,
		"file_name": None,
		"note": None,
		"duration_seconds": None,
		"loop_video": bool(cint(settings.karaoke_loop_background_video)),
		"ignore_background_audio": bool(cint(settings.karaoke_ignore_background_audio)),
	}

	candidates: list[tuple[str, str]] = []
	if job.get("karaoke_background_video_file"):
		candidates.append((SOURCE_JOB_UPLOAD, job.karaoke_background_video_file))
	if settings.get("default_karaoke_background_video"):
		candidates.append((SOURCE_SETTINGS_DEFAULT, settings.default_karaoke_background_video))

	for source, file_url in candidates:
		local_path = _resolve_local_video_path(file_url)
		if not local_path:
			continue
		duration = probe_media_duration(local_path)
		result.update(
			{
				"source": source,
				"local_path": local_path,
				"file_url": file_url,
				"file_name": _safe_file_name(file_url),
				"duration_seconds": duration,
			}
		)
		return result

	result["note"] = _("No background video configured. A generated color background will be used.")
	return result


def build_background_video_filter(settings, width: int, height: int) -> str:
	"""Build ffmpeg -vf filter chain for background scaling and optional effects."""
	fit_mode = (settings.karaoke_background_fit_mode or "Cover").strip()
	width = int(width)
	height = int(height)

	if fit_mode == "Contain":
		base = (
			f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
			f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
		)
	elif fit_mode == "Stretch":
		base = f"scale={width}:{height}"
	else:
		base = (
			f"scale={width}:{height}:force_original_aspect_ratio=increase,"
			f"crop={width}:{height}"
		)

	filters = [base]
	if cint(settings.karaoke_background_blur):
		filters.append("gblur=sigma=3")
	if cint(settings.karaoke_background_darken):
		opacity = flt(settings.karaoke_background_darken_opacity)
		opacity = min(max(opacity, 0.0), 1.0)
		filters.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=black@{opacity}:t=fill")

	return ",".join(filters)


def prepare_background_video_for_render(job, audio_duration_seconds: float) -> tuple[str | None, dict]:
	"""Prepare background MP4 for karaoke render. Returns (path, metadata)."""
	settings = get_settings()
	resolved = resolve_karaoke_background_video(job)
	audio_duration = max(flt(audio_duration_seconds), 0.1)
	width = cint(settings.karaoke_video_width) or 1080
	height = cint(settings.karaoke_video_height) or 1920

	metadata = {
		"karaoke_background_source": resolved["source"],
		"karaoke_background_note": resolved.get("note"),
		"karaoke_background_duration_seconds": resolved.get("duration_seconds"),
	}

	if not resolved.get("local_path"):
		metadata["karaoke_background_source"] = SOURCE_GENERATED_COLOR
		return None, metadata

	video_duration = flt(resolved.get("duration_seconds") or 0)
	loop_video = bool(resolved.get("loop_video"))
	target_duration = audio_duration
	note_parts: list[str] = []

	if video_duration > 0 and video_duration < audio_duration:
		if loop_video:
			note_parts.append(_("Background video is shorter than the karaoke audio and will be looped."))
		else:
			target_duration = video_duration
			note_parts.append(
				_("Background video is shorter than the karaoke audio and looping is disabled.")
			)
	elif video_duration > audio_duration:
		note_parts.append(_("Background video is longer than the karaoke audio and will be trimmed."))

	if note_parts:
		metadata["karaoke_background_note"] = " ".join(str(part) for part in note_parts)

	from audio_stem.utils.karaoke_subtitles import resolve_karaoke_audio_path

	audio_path = resolve_karaoke_audio_path(job)
	output_path = tempfile.mktemp(suffix=".mp4")
	ensure_ffmpeg_available()

	video_filter = build_background_video_filter(settings, width, height)
	prepare_video_background_with_audio(
		video_path=resolved["local_path"],
		audio_path=audio_path,
		output_path=output_path,
		duration_seconds=target_duration,
		width=width,
		height=height,
		loop_video=loop_video,
		video_filter=video_filter,
		ignore_background_audio=bool(resolved.get("ignore_background_audio")),
		preset=(settings.karaoke_ffmpeg_preset or "veryfast").strip(),
		crf=cint(settings.karaoke_ffmpeg_crf) or 18,
	)
	return output_path, metadata


def apply_background_metadata(job, metadata: dict) -> None:
	for fieldname in (
		"karaoke_background_source",
		"karaoke_background_note",
		"karaoke_background_duration_seconds",
	):
		if fieldname in metadata:
			job.set(fieldname, metadata.get(fieldname))

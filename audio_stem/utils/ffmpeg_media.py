# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
import os
import shutil
import subprocess
import tempfile

import frappe
from frappe import _


def is_ffprobe_available() -> bool:
	return bool(shutil.which("ffprobe"))


def ensure_ffprobe_available():
	if not is_ffprobe_available():
		frappe.throw(
			_("ffprobe is required for karaoke video rendering but was not found on the server PATH."),
			frappe.ValidationError,
		)


def is_ffmpeg_available() -> bool:
	return bool(shutil.which("ffmpeg"))


def ensure_ffmpeg_available():
	if not is_ffmpeg_available():
		frappe.throw(
			_("ffmpeg is required for karaoke video generation but was not found on the server PATH."),
			frappe.ValidationError,
		)


def _run_ffmpeg(args: list[str], *, error_title: str = "ffmpeg failed"):
	try:
		result = subprocess.run(
			["ffmpeg", "-y", *args],
			capture_output=True,
			text=True,
			check=False,
		)
	except FileNotFoundError as exc:
		ensure_ffmpeg_available()
		raise exc
	if result.returncode != 0:
		frappe.log_error(title=error_title, message=result.stderr or result.stdout or "ffmpeg error")
		frappe.throw(_("Media processing failed. Please contact an administrator."), frappe.ValidationError)


def probe_media_duration(path: str) -> float | None:
	"""Return media duration in seconds using ffprobe, or None when unavailable."""
	if not path or not os.path.isfile(path):
		return None
	if not is_ffprobe_available():
		return None

	try:
		result = subprocess.run(
			[
				"ffprobe",
				"-v",
				"error",
				"-show_entries",
				"format=duration",
				"-of",
				"json",
				path,
			],
			capture_output=True,
			text=True,
			check=False,
		)
	except OSError:
		return None

	if result.returncode != 0:
		return None

	try:
		payload = json.loads(result.stdout or "{}")
	except json.JSONDecodeError:
		return None

	format_section = payload.get("format")
	if not isinstance(format_section, dict):
		return None

	duration_value = format_section.get("duration")
	if duration_value is None:
		return None

	try:
		return float(duration_value)
	except (TypeError, ValueError):
		return None


def build_prepare_video_background_ffmpeg_args(
	*,
	video_path: str,
	audio_path: str,
	output_path: str,
	duration_seconds: float,
	width: int,
	height: int,
	loop_video: bool = True,
	video_filter: str | None = None,
	ignore_background_audio: bool = True,
	preset: str = "veryfast",
	crf: int = 18,
) -> list[str]:
	"""Build ffmpeg args to scale/crop a background video and mux karaoke audio."""
	duration = max(float(duration_seconds), 0.1)
	if not video_filter:
		video_filter = (
			f"scale={width}:{height}:force_original_aspect_ratio=increase,"
			f"crop={width}:{height}"
		)
	args: list[str] = []
	if loop_video:
		args.extend(["-stream_loop", "-1"])
	args.extend(["-i", video_path, "-i", audio_path, "-t", str(duration), "-vf", video_filter])
	if ignore_background_audio:
		args.extend(["-map", "0:v:0", "-map", "1:a:0"])
	else:
		args.extend(["-map", "0:v:0", "-map", "1:a:0"])
	args.extend(
		[
			"-c:v",
			"libx264",
			"-preset",
			preset,
			"-crf",
			str(crf),
			"-pix_fmt",
			"yuv420p",
			"-c:a",
			"aac",
			output_path,
		]
	)
	return args


def prepare_video_background_with_audio(
	*,
	video_path: str,
	audio_path: str,
	output_path: str,
	duration_seconds: float,
	width: int,
	height: int,
	loop_video: bool = True,
	video_filter: str | None = None,
	ignore_background_audio: bool = True,
	preset: str = "veryfast",
	crf: int = 18,
):
	"""Loop/trim, scale/crop background video and mux karaoke audio."""
	_run_ffmpeg(
		build_prepare_video_background_ffmpeg_args(
			video_path=video_path,
			audio_path=audio_path,
			output_path=output_path,
			duration_seconds=duration_seconds,
			width=width,
			height=height,
			loop_video=loop_video,
			video_filter=video_filter,
			ignore_background_audio=ignore_background_audio,
			preset=preset,
			crf=crf,
		),
		error_title="ffmpeg karaoke background video preparation failed",
	)
	return output_path


def transcode_audio_mono_mp3(input_path: str, *, bitrate: str = "64k") -> str:
	output_path = tempfile.mktemp(suffix=".mp3")
	_run_ffmpeg(
		[
			"-i",
			input_path,
			"-ac",
			"1",
			"-ar",
			"16000",
			"-b:a",
			bitrate,
			output_path,
		],
		error_title="ffmpeg audio transcode failed",
	)
	return output_path


def create_color_video_with_audio(
	*,
	output_path: str,
	duration_seconds: float,
	width: int,
	height: int,
	background_color: str,
	audio_path: str,
):
	color = (background_color or "#111111").lstrip("#")
	if len(color) == 3:
		color = "".join(ch * 2 for ch in color)
	_run_ffmpeg(
		[
			"-f",
			"lavfi",
			"-i",
			f"color=c=0x{color}:s={width}x{height}:d={max(duration_seconds, 1)}",
			"-i",
			audio_path,
			"-shortest",
			"-c:v",
			"libx264",
			"-pix_fmt",
			"yuv420p",
			"-c:a",
			"aac",
			output_path,
		],
		error_title="ffmpeg background video creation failed",
	)
	return output_path


def get_file_size_mb(path: str) -> float:
	return os.path.getsize(path) / (1024 * 1024)

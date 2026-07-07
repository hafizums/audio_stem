# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os
import shutil
import subprocess
import tempfile

import frappe
from frappe import _


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

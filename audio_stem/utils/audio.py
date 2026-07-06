# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import os


def get_audio_duration_seconds(local_audio_path: str) -> int | None:
	if not local_audio_path or not os.path.exists(local_audio_path):
		return None

	try:
		from mutagen import File as MutagenFile

		audio = MutagenFile(local_audio_path)
		if audio and audio.info and getattr(audio.info, "length", None):
			return int(audio.info.length)
	except Exception:
		return None

	return None

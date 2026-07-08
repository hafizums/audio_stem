# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Build karaoke-friendly segments from Scribe word timestamps."""

from __future__ import annotations

from frappe.utils import flt


def group_words_into_segments(
	words: list[dict],
	*,
	max_words: int = 8,
	max_gap_seconds: float = 1.2,
	max_duration_seconds: float = 6.0,
) -> list[dict]:
	if not words:
		return []

	sorted_words = sorted(words, key=lambda item: (flt(item.get("start")), flt(item.get("end"))))
	segments: list[dict] = []
	current_words: list[dict] = []

	def flush():
		if not current_words:
			return
		text = " ".join((word.get("word") or "").strip() for word in current_words if (word.get("word") or "").strip())
		if not text:
			current_words.clear()
			return
		segments.append(
			{
				"id": len(segments),
				"start": flt(current_words[0].get("start")),
				"end": flt(current_words[-1].get("end")),
				"text": text,
			}
		)
		current_words.clear()

	for word in sorted_words:
		text = (word.get("word") or "").strip()
		if not text:
			continue
		if not current_words:
			current_words.append(word)
			continue

		prev = current_words[-1]
		gap = flt(word.get("start")) - flt(prev.get("end"))
		segment_start = flt(current_words[0].get("start"))
		segment_end = flt(current_words[-1].get("end"))
		next_end = flt(word.get("end"))
		too_many_words = len(current_words) >= max_words
		gap_too_large = gap > max_gap_seconds
		duration_too_long = max(segment_end, next_end) - segment_start > max_duration_seconds

		if too_many_words or gap_too_large or duration_too_long:
			flush()
		current_words.append(word)

	flush()
	return segments

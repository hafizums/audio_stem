# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import re

from frappe import _

_SENSITIVE_PATTERNS = (
	re.compile(r"api[_ -]?key", re.IGNORECASE),
	re.compile(r"WAVESPEED_API_KEY", re.IGNORECASE),
	re.compile(r"Bearer\s+\S+", re.IGNORECASE),
	re.compile(r"Traceback \(most recent call last\):", re.IGNORECASE),
)


def safe_error_message(exc: Exception, *, max_length: int = 500) -> str:
	raw = str(exc) or exc.__class__.__name__

	for pattern in _SENSITIVE_PATTERNS:
		if pattern.search(raw):
			return _("Audio processing failed. Please try again or contact an administrator.")

	lower = raw.lower()
	if "authentication is required" in lower or "http 401" in lower:
		return _("Audio processing service authentication failed. Please contact an administrator.")

	if "http 403" in lower:
		return _("Audio processing request was denied. Please contact an administrator.")

	if "timeout" in lower:
		return _("Audio processing timed out. Please try again later.")

	if "file not found" in lower or "could not resolve the attached audio file" in lower:
		return _("The uploaded audio file could not be found. Please upload the file again.")

	if len(raw) > max_length:
		return raw[: max_length - 3] + "..."
	return raw

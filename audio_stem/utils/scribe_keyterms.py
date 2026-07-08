# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Parse and validate ElevenLabs Scribe keyterms."""

from __future__ import annotations

import re

import frappe
from frappe import _

MAX_KEYTERMS = 1000
MAX_KEYTERM_CHARS = 50
MAX_KEYTERM_WORDS = 5
FORBIDDEN_KEYTERM_CHARS = set("<>{}[]\\")
SECRET_KEYTERM_PATTERNS = (
	re.compile(r"sk-[a-z0-9]{8,}", re.IGNORECASE),
	re.compile(r"api[_ -]?key", re.IGNORECASE),
)


def parse_keyterms(text: str | None) -> list[str]:
	if not text:
		return []
	terms: list[str] = []
	seen: set[str] = set()
	for raw_line in str(text).replace(",", "\n").splitlines():
		term = " ".join(raw_line.strip().split())
		if not term:
			continue
		key = term.lower()
		if key in seen:
			continue
		seen.add(key)
		terms.append(term)
	return terms


def validate_keyterms(keyterms: list[str] | None) -> None:
	if not keyterms:
		return
	if len(keyterms) > MAX_KEYTERMS:
		frappe.throw(
			_("ElevenLabs keyterms are limited to {0} entries.").format(MAX_KEYTERMS),
			frappe.ValidationError,
		)

	for term in keyterms:
		cleaned = (term or "").strip()
		if not cleaned:
			continue
		if len(cleaned) >= MAX_KEYTERM_CHARS:
			frappe.throw(
				_("Each ElevenLabs keyterm must be shorter than {0} characters.").format(MAX_KEYTERM_CHARS),
				frappe.ValidationError,
			)
		if len(cleaned.split()) > MAX_KEYTERM_WORDS:
			frappe.throw(
				_("Each ElevenLabs keyterm may contain at most {0} words.").format(MAX_KEYTERM_WORDS),
				frappe.ValidationError,
			)
		if any(char in cleaned for char in FORBIDDEN_KEYTERM_CHARS):
			frappe.throw(
				_("ElevenLabs keyterms contain unsupported characters."),
				frappe.ValidationError,
			)
		for pattern in SECRET_KEYTERM_PATTERNS:
			if pattern.search(cleaned):
				frappe.throw(_("ElevenLabs keyterms must not contain secrets."), frappe.ValidationError)

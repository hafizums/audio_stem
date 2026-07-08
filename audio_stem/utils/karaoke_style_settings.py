# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

"""Karaoke style settings validation and karaoke_engine option builders."""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.limits import get_settings

CLASSIC_CENTER_PRESET = "classic_center_3line"
KARAOKE_STYLE_PRESETS = ("default_1080p", "default_720p", "mobile_1080x1920", CLASSIC_CENTER_PRESET)
JOB_OVERRIDE_STYLE_PRESETS = ("default_1080p", "mobile_1080x1920", CLASSIC_CENTER_PRESET)
KARAOKE_FONT_NAMES = ("Helvetica", "Arial Narrow", "Bebas Neue")
KARAOKE_STYLE_SOURCES = ("Global Settings", "Job Override")

KARAOKE_TIMING_GRANULARITIES = ("word", "syllable", "character")
KARAOKE_SYLLABLE_MODES = ("auto", "simple_vowel", "hyphen")

STYLE_FIELD_KEYS = (
	"karaoke_style_preset",
	"karaoke_visible_lines",
	"karaoke_center_y_percent",
	"karaoke_line_gap",
	"karaoke_font_size",
	"karaoke_primary_color",
	"karaoke_highlight_color",
	"karaoke_previous_line_color",
	"karaoke_next_line_color",
	"karaoke_outline_color",
	"karaoke_shadow",
	"karaoke_outline",
	"karaoke_timing_granularity",
	"karaoke_syllable_mode",
)

GLOBAL_TO_OVERRIDE_FIELD = {
	"karaoke_style_preset": "karaoke_style_preset_override",
	"karaoke_visible_lines": "karaoke_visible_lines_override",
	"karaoke_center_y_percent": "karaoke_center_y_percent_override",
	"karaoke_line_gap": "karaoke_line_gap_override",
	"karaoke_font_size": "karaoke_font_size_override",
	"karaoke_primary_color": "karaoke_primary_color_override",
	"karaoke_highlight_color": "karaoke_highlight_color_override",
	"karaoke_previous_line_color": "karaoke_previous_line_color_override",
	"karaoke_next_line_color": "karaoke_next_line_color_override",
	"karaoke_outline_color": "karaoke_outline_color_override",
	"karaoke_shadow": "karaoke_shadow_override",
	"karaoke_outline": "karaoke_outline_override",
	"karaoke_timing_granularity": "karaoke_timing_granularity_override",
	"karaoke_syllable_mode": "karaoke_syllable_mode_override",
}

JOB_STYLE_UPDATE_FIELDS = ("karaoke_style_override_enabled", *GLOBAL_TO_OVERRIDE_FIELD.values())


def validate_karaoke_style_settings(doc) -> None:
	"""Validate karaoke style fields on Audio Separation Settings."""
	_validate_style_values(_style_values_from_settings_doc(doc))


def validate_job_karaoke_style_overrides(job) -> None:
	"""Validate merged effective style when job override is enabled."""
	if not cint(job.karaoke_style_override_enabled):
		return
	if _has_override_value(job.karaoke_style_preset_override):
		preset = (job.karaoke_style_preset_override or "").strip()
		if preset not in JOB_OVERRIDE_STYLE_PRESETS:
			frappe.throw(
				_("Karaoke Style Preset Override must be one of: {0}").format(
					", ".join(JOB_OVERRIDE_STYLE_PRESETS)
				)
			)
	_validate_style_values(resolve_effective_karaoke_style(job)["effective"])


def _style_values_from_settings_doc(doc) -> dict:
	return {
		"karaoke_style_preset": doc.karaoke_style_preset or "default_1080p",
		"karaoke_visible_lines": doc.karaoke_visible_lines,
		"karaoke_center_y_percent": doc.karaoke_center_y_percent,
		"karaoke_line_gap": doc.karaoke_line_gap,
		"karaoke_font_size": doc.karaoke_font_size,
		"karaoke_font_name": doc.karaoke_font_name,
		"karaoke_primary_color": doc.karaoke_primary_color,
		"karaoke_highlight_color": doc.karaoke_highlight_color,
		"karaoke_previous_line_color": doc.karaoke_previous_line_color,
		"karaoke_next_line_color": doc.karaoke_next_line_color,
		"karaoke_outline_color": doc.karaoke_outline_color,
		"karaoke_shadow": doc.karaoke_shadow,
		"karaoke_outline": doc.karaoke_outline,
		"karaoke_timing_granularity": doc.karaoke_timing_granularity,
		"karaoke_syllable_mode": doc.karaoke_syllable_mode,
	}


def _validate_style_values(style: dict) -> None:
	preset = (style.get("karaoke_style_preset") or "default_1080p").strip()
	if preset not in KARAOKE_STYLE_PRESETS:
		frappe.throw(
			_("Karaoke Style Preset must be one of: {0}").format(", ".join(KARAOKE_STYLE_PRESETS))
		)

	visible_lines = cint(style.get("karaoke_visible_lines")) or 3
	if not 1 <= visible_lines <= 5:
		frappe.throw(_("Karaoke Visible Lines must be between 1 and 5."))

	center_y = flt(style.get("karaoke_center_y_percent")) or 50.0
	if not 10.0 <= center_y <= 90.0:
		frappe.throw(_("Karaoke Center Y Percent must be between 10 and 90."))

	font_size = cint(style.get("karaoke_font_size")) or 64
	if not 24 <= font_size <= 120:
		frappe.throw(_("Karaoke Font Size must be between 24 and 120."))

	font_name = (style.get("karaoke_font_name") or "Helvetica").strip()
	if font_name not in KARAOKE_FONT_NAMES:
		frappe.throw(
			_("Karaoke Font Name must be one of: {0}").format(", ".join(KARAOKE_FONT_NAMES))
		)

	line_gap = cint(style.get("karaoke_line_gap")) or 90
	if not 20 <= line_gap <= 300:
		frappe.throw(_("Karaoke Line Gap must be between 20 and 300."))

	for fieldname in (
		"karaoke_primary_color",
		"karaoke_highlight_color",
		"karaoke_previous_line_color",
		"karaoke_next_line_color",
		"karaoke_outline_color",
	):
		_validate_hex_color(style.get(fieldname), fieldname)

	if flt(style.get("karaoke_shadow")) < 0:
		frappe.throw(_("Karaoke Shadow must be >= 0."))
	if flt(style.get("karaoke_outline")) < 0:
		frappe.throw(_("Karaoke Outline must be >= 0."))

	timing_granularity = _normalize_timing_granularity(
		style.get("karaoke_timing_granularity")
	)
	if timing_granularity not in KARAOKE_TIMING_GRANULARITIES:
		frappe.throw(
			_("Karaoke Timing Granularity must be one of: {0}").format(
				", ".join(KARAOKE_TIMING_GRANULARITIES)
			)
		)

	syllable_mode = _normalize_syllable_mode(style.get("karaoke_syllable_mode"))
	if syllable_mode not in KARAOKE_SYLLABLE_MODES:
		frappe.throw(
			_("Karaoke Syllable Mode must be one of: {0}").format(
				", ".join(KARAOKE_SYLLABLE_MODES)
			)
		)


def _normalize_timing_granularity(value: str | None) -> str:
	return (value or "word").strip().lower()


def _normalize_syllable_mode(value: str | None) -> str:
	return (value or "auto").strip().lower()


def _validate_hex_color(value: str | None, fieldname: str) -> None:
	from karaoke_engine.ass.colors import is_valid_hex_color

	if not value:
		return
	if not is_valid_hex_color(value):
		frappe.throw(_("{0} must be a valid hex color like #FFFFFF.").format(fieldname))


def _has_override_value(value) -> bool:
	return value is not None and str(value).strip() != ""


def _normalize_style_dict(style: dict) -> dict:
	return {
		"karaoke_style_preset": (style.get("karaoke_style_preset") or "default_1080p").strip(),
		"karaoke_visible_lines": cint(style.get("karaoke_visible_lines")) or 3,
		"karaoke_center_y_percent": flt(style.get("karaoke_center_y_percent")) or 50.0,
		"karaoke_line_gap": cint(style.get("karaoke_line_gap")) or 90,
		"karaoke_font_name": (style.get("karaoke_font_name") or "Helvetica").strip(),
		"karaoke_font_size": cint(style.get("karaoke_font_size")) or 64,
		"karaoke_primary_color": (style.get("karaoke_primary_color") or "#FFFFFF").strip(),
		"karaoke_highlight_color": (style.get("karaoke_highlight_color") or "#3366FF").strip(),
		"karaoke_previous_line_color": (style.get("karaoke_previous_line_color") or "#3366FF").strip(),
		"karaoke_next_line_color": (style.get("karaoke_next_line_color") or "#FFFFFF").strip(),
		"karaoke_outline_color": (style.get("karaoke_outline_color") or "#000000").strip(),
		"karaoke_shadow": flt(style.get("karaoke_shadow")) or 1.0,
		"karaoke_outline": flt(style.get("karaoke_outline")) or 3.0,
		"karaoke_timing_granularity": _normalize_timing_granularity(
			style.get("karaoke_timing_granularity")
		),
		"karaoke_syllable_mode": _normalize_syllable_mode(
			style.get("karaoke_syllable_mode")
		),
	}


def karaoke_style_settings_payload(settings=None) -> dict:
	settings = settings or get_settings()
	return _normalize_style_dict(_style_values_from_settings_doc(settings))


def job_style_override_payload(job) -> dict:
	return {
		"karaoke_style_override_enabled": bool(cint(job.karaoke_style_override_enabled)),
		"karaoke_style_preset_override": job.karaoke_style_preset_override,
		"karaoke_visible_lines_override": job.karaoke_visible_lines_override,
		"karaoke_center_y_percent_override": job.karaoke_center_y_percent_override,
		"karaoke_line_gap_override": job.karaoke_line_gap_override,
		"karaoke_font_size_override": job.karaoke_font_size_override,
		"karaoke_primary_color_override": job.karaoke_primary_color_override,
		"karaoke_highlight_color_override": job.karaoke_highlight_color_override,
		"karaoke_previous_line_color_override": job.karaoke_previous_line_color_override,
		"karaoke_next_line_color_override": job.karaoke_next_line_color_override,
		"karaoke_outline_color_override": job.karaoke_outline_color_override,
		"karaoke_shadow_override": job.karaoke_shadow_override,
		"karaoke_outline_override": job.karaoke_outline_override,
		"karaoke_timing_granularity_override": job.karaoke_timing_granularity_override,
		"karaoke_syllable_mode_override": job.karaoke_syllable_mode_override,
	}


def resolve_effective_karaoke_style(job, settings=None) -> dict:
	"""Resolve global + optional per-job karaoke style overrides."""
	settings = settings or get_settings()
	global_style = karaoke_style_settings_payload(settings)
	override_enabled = bool(cint(getattr(job, "karaoke_style_override_enabled", 0)))
	override_values = job_style_override_payload(job)

	if not override_enabled:
		return {
			"source": "Global Settings",
			"override_enabled": False,
			"global_style": global_style,
			"override_style": override_values,
			"effective": global_style,
		}

	merged = dict(global_style)
	for field, override_field in GLOBAL_TO_OVERRIDE_FIELD.items():
		override_value = getattr(job, override_field, None)
		if _has_override_value(override_value):
			merged[field] = override_value

	effective = _normalize_style_dict(merged)
	_validate_style_values(effective)
	return {
		"source": "Job Override",
		"override_enabled": True,
		"global_style": global_style,
		"override_style": override_values,
		"effective": effective,
	}


def parse_stored_effective_style_json(job) -> dict | None:
	raw = getattr(job, "karaoke_effective_style_json", None)
	if not raw:
		return None
	try:
		parsed = json.loads(raw)
	except (TypeError, json.JSONDecodeError):
		return None
	return parsed if isinstance(parsed, dict) else None


def is_classic_center_preset(preset: str | None = None, settings=None) -> bool:
	if preset is not None:
		return (preset or "").strip().lower() == CLASSIC_CENTER_PRESET
	settings = settings or get_settings()
	resolved = (settings.karaoke_style_preset or "default_1080p").strip().lower()
	return resolved == CLASSIC_CENTER_PRESET


def build_classic_center_options_from_style(style: dict):
	"""Build karaoke_engine ClassicCenterStyleOptions from a resolved style dict."""
	from karaoke_engine import ClassicCenterStyleOptions

	normalized = _normalize_style_dict(style)
	return ClassicCenterStyleOptions(
		visible_lines=normalized["karaoke_visible_lines"],
		active_line_index=1,
		font_name=normalized["karaoke_font_name"],
		font_size=normalized["karaoke_font_size"],
		line_gap=normalized["karaoke_line_gap"],
		primary_color=normalized["karaoke_primary_color"],
		highlight_color=normalized["karaoke_highlight_color"],
		previous_line_color=normalized["karaoke_previous_line_color"],
		next_line_color=normalized["karaoke_next_line_color"],
		outline_color=normalized["karaoke_outline_color"],
		shadow=normalized["karaoke_shadow"],
		outline=normalized["karaoke_outline"],
		center_y_percent=normalized["karaoke_center_y_percent"],
	)


def build_classic_center_options_from_settings(settings=None):
	"""Build karaoke_engine ClassicCenterStyleOptions from Audio Separation Settings."""
	settings = settings or get_settings()
	return build_classic_center_options_from_style(karaoke_style_settings_payload(settings))


def karaoke_style_for_job_payload(job, settings=None) -> dict:
	"""API-safe payload for per-job karaoke style resolution."""
	resolved = resolve_effective_karaoke_style(job, settings=settings)
	stored_effective = parse_stored_effective_style_json(job)
	return {
		"override_enabled": resolved["override_enabled"],
		"global_style": resolved["global_style"],
		"override_style": resolved["override_style"],
		"effective_style": resolved["effective"],
		"style_source": resolved["source"],
		"rendered_style_source": job.get("karaoke_style_source"),
		"rendered_effective_style": stored_effective,
		"has_karaoke_ass": bool(job.get("karaoke_ass_file")),
		"has_karaoke_video": bool(job.get("karaoke_video_file")),
		"needs_regenerate_for_style": _needs_regenerate_for_style(job, resolved["effective"]),
	}


def _needs_regenerate_for_style(job, current_effective: dict) -> bool:
	if not job.get("karaoke_ass_file"):
		return False
	stored = parse_stored_effective_style_json(job)
	if not stored:
		return True
	return _normalize_style_dict(stored) != _normalize_style_dict(current_effective)


def apply_job_style_update(job, style_payload: dict) -> None:
	"""Apply validated per-job karaoke style override fields."""
	if "karaoke_style_override_enabled" in style_payload:
		job.karaoke_style_override_enabled = 1 if cint(style_payload.get("karaoke_style_override_enabled")) else 0

	for override_field in GLOBAL_TO_OVERRIDE_FIELD.values():
		if override_field not in style_payload:
			continue
		value = style_payload.get(override_field)
		if value is None or value == "":
			job.set(override_field, None)
		else:
			job.set(override_field, value)

	validate_job_karaoke_style_overrides(job)


def clear_job_style_overrides(job) -> None:
	job.karaoke_style_override_enabled = 0
	for override_field in GLOBAL_TO_OVERRIDE_FIELD.values():
		job.set(override_field, None)

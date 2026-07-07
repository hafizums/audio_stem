# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import frappe
from frappe import _
from frappe.utils import cint

from audio_stem.utils.limits import get_settings


def _is_system_manager(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or "System Manager" in frappe.get_roles(user)


def _parse_list_field(value: str | None) -> list[str]:
	if not value:
		return []
	return [item.strip() for item in value.replace(",", "\n").splitlines() if item.strip()]


def is_pilot_access_allowed(user: str | None = None) -> bool:
	user = user or frappe.session.user
	settings = get_settings()

	if not cint(settings.pilot_mode_enabled):
		return True

	if _is_system_manager(user):
		return True

	blocked_users = _parse_list_field(settings.blocked_users)
	if user in blocked_users:
		return False

	allowed_users = _parse_list_field(settings.allowed_users)
	if user in allowed_users:
		return True

	allowed_roles = _parse_list_field(settings.allowed_roles)
	if allowed_roles:
		user_roles = set(frappe.get_roles(user))
		if user_roles.intersection(allowed_roles):
			return True

	if allowed_users or allowed_roles:
		return False

	return True


def ensure_pilot_access(user: str | None = None):
	if not is_pilot_access_allowed(user):
		frappe.throw(
			_("Audio separation is currently limited to pilot users. Please contact an administrator."),
			frappe.PermissionError,
		)


def get_pilot_access_status(user: str | None = None) -> dict:
	user = user or frappe.session.user
	settings = get_settings()
	enabled = bool(cint(settings.pilot_mode_enabled))
	allowed = is_pilot_access_allowed(user)
	return {
		"pilot_mode_enabled": enabled,
		"pilot_access_allowed": allowed,
		"blocked_reason": None
		if allowed
		else _("Audio separation is currently limited to pilot users. Please contact an administrator."),
	}

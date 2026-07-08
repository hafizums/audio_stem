# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

import json
import traceback

import frappe
from frappe import _
from frappe.utils import cint, flt

from audio_stem.utils.errors import safe_error_message
from audio_stem.utils.limits import get_settings

DEFAULT_BASE_URL = "https://llm.wavespeed.ai/v1"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"


def is_wavespeed_llm_configured(settings=None) -> bool:
	settings = settings or get_settings()
	api_key = (settings.get_password("wavespeed_llm_api_key", raise_exception=False) or "").strip()
	base_url = (settings.wavespeed_llm_base_url or DEFAULT_BASE_URL).strip()
	model = (settings.wavespeed_llm_model or DEFAULT_MODEL).strip()
	return bool(api_key and base_url and model)


def get_wavespeed_llm_api_key(settings=None) -> str:
	settings = settings or get_settings()
	api_key = (settings.get_password("wavespeed_llm_api_key", raise_exception=False) or "").strip()
	if not api_key:
		frappe.throw(
			_("WaveSpeed LLM API key is not configured in Audio Separation Settings."),
			frappe.ValidationError,
		)
	return api_key


def resolve_wavespeed_llm_base_url(settings=None) -> str:
	settings = settings or get_settings()
	return (settings.wavespeed_llm_base_url or DEFAULT_BASE_URL).strip().rstrip("/")


def resolve_wavespeed_llm_model(settings=None, *, use_reasoning_model: bool = False) -> str:
	settings = settings or get_settings()
	if use_reasoning_model and (settings.wavespeed_llm_reasoning_model or "").strip():
		return settings.wavespeed_llm_reasoning_model.strip()
	return (settings.wavespeed_llm_model or DEFAULT_MODEL).strip()


def get_wavespeed_llm_client(settings=None):
	settings = settings or get_settings()
	from openai import OpenAI

	return OpenAI(
		api_key=get_wavespeed_llm_api_key(settings),
		base_url=resolve_wavespeed_llm_base_url(settings),
	)


def _parse_json_content(content: str) -> dict:
	text = (content or "").strip()
	if not text:
		frappe.throw(_("WaveSpeed LLM returned an empty response."), frappe.ValidationError)

	try:
		parsed = json.loads(text)
	except json.JSONDecodeError:
		start = text.find("{")
		end = text.rfind("}")
		if start >= 0 and end > start:
			try:
				parsed = json.loads(text[start : end + 1])
			except json.JSONDecodeError:
				frappe.log_error(title="WaveSpeed LLM JSON Parse Error", message=traceback.format_exc())
				frappe.throw(
					_("WaveSpeed LLM returned invalid JSON. Please try again."),
					frappe.ValidationError,
				)
		else:
			frappe.log_error(title="WaveSpeed LLM JSON Parse Error", message=traceback.format_exc())
			frappe.throw(
				_("WaveSpeed LLM returned invalid JSON. Please try again."),
				frappe.ValidationError,
			)

	if not isinstance(parsed, dict):
		frappe.throw(_("WaveSpeed LLM response must be a JSON object."), frappe.ValidationError)
	return parsed


def chat_completions_json(
	messages: list[dict],
	*,
	model: str | None = None,
	temperature: float | None = None,
	timeout: int | None = None,
	use_reasoning_model: bool = False,
	settings=None,
) -> dict:
	settings = settings or get_settings()
	client = get_wavespeed_llm_client(settings)
	selected_model = model or resolve_wavespeed_llm_model(settings, use_reasoning_model=use_reasoning_model)
	temp = flt(temperature if temperature is not None else settings.wavespeed_llm_temperature)
	timeout_seconds = cint(timeout if timeout is not None else settings.wavespeed_llm_timeout_seconds) or 120

	try:
		response = client.chat.completions.create(
			model=selected_model,
			messages=messages,
			temperature=temp,
			response_format={"type": "json_object"},
			timeout=timeout_seconds,
		)
	except Exception as exc:
		frappe.log_error(title="WaveSpeed LLM Request Failed", message=traceback.format_exc())
		raise frappe.ValidationError(safe_error_message(exc))

	choice = (response.choices or [None])[0]
	content = getattr(getattr(choice, "message", None), "content", None) if choice else None
	parsed = _parse_json_content(content or "")

	usage = getattr(response, "usage", None)
	input_tokens = cint(getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", None) or 0)
	output_tokens = cint(getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", None) or 0)

	return {
		"model": selected_model,
		"provider": "WaveSpeed LLM",
		"parsed": parsed,
		"raw_response": response.model_dump() if hasattr(response, "model_dump") else {"content": content},
		"input_tokens": input_tokens,
		"output_tokens": output_tokens,
	}

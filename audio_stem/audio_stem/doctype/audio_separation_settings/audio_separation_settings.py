# Copyright (c) 2026, Hafiz and contributors
# License: MIT. See LICENSE

from frappe.model.document import Document
from frappe.utils import cint

from audio_stem.utils.karaoke_style_settings import validate_karaoke_style_settings
from audio_stem.utils.scribe_keyterms import parse_keyterms, validate_keyterms
from audio_stem.utils.transcription_quality import validate_transcription_prompt_text


class AudioSeparationSettings(Document):
	def validate(self):
		validate_karaoke_style_settings(self)
		if self.transcription_prompt_enabled:
			validate_transcription_prompt_text(self.transcription_prompt_text)
			from audio_stem.utils.transcription_quality import _reject_instruction_style_prompt

			if (self.transcription_prompt_text or "").strip():
				_reject_instruction_style_prompt(self.transcription_prompt_text)
		if self.elevenlabs_scribe_model:
			from audio_stem.integrations.elevenlabs_scribe_client import resolve_scribe_model

			resolve_scribe_model(self.elevenlabs_scribe_model)
		if cint(self.elevenlabs_use_keyterms):
			validate_keyterms(parse_keyterms(self.elevenlabs_keyterms))


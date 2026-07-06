from frappe import _

def get_data():
	return [
		{
			"module_name": "Audio Stem",
			"type": "module",
			"label": _("Audio Stem")
		},
		{
			"type": "page",
			"name": "audio-vocal-remover",
			"label": _("Audio Vocal Remover"),
			"link": "audio-vocal-remover",
		},
	]

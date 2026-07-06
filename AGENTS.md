# Audio Stem MVP Agent Instructions

This app is a Frappe Framework app named `audio_stem`.

Goal:
Build an internal MVP for AI vocal and instrumental separation using WaveSpeed model `wavespeed-ai/audio-vocal-isolator`.

Scope for Milestone 1:

* Create a Frappe app feature for uploading one audio file.
* Create an `Audio Separation Job` DocType.
* Create an `Audio Separation Settings` Single DocType.
* Store WaveSpeed API key in the settings DocType as a password field.
* Use Frappe background jobs for processing.
* Use the WaveSpeed Python SDK only from backend worker code.
* Never call WaveSpeed from frontend JavaScript.
* Never expose the WaveSpeed API key to the browser.
* Save output URLs for vocal and instrumental tracks.
* Add a simple button on the job form to start processing.
* No payment integration.
* No public landing page.
* No subscription logic.
* No multi-stem drums/bass/other output yet.

Architecture rules:

* API methods should be thin.
* Provider-specific code belongs in `audio_stem/integrations/wavespeed_client.py`.
* Background processing belongs in `audio_stem/workers/separation_worker.py`.
* Do not edit unrelated apps.
* Do not change ERPNext core.
* Do not add payment code in this milestone.
* Do not store provider secrets in JavaScript or logs.

The WaveSpeed model output order must be treated as:

1. `outputs[0]` = vocal track
2. `outputs[1]` = instrumental track

Before making changes:

* Inspect the existing `audio_stem` app structure.
* Create missing folders only if needed.
* Keep implementation small and testable.

const ACTIVE_STATUSES = ["Queued", "Uploading", "Processing"];
const TERMINAL_STATUSES = ["Completed", "Failed", "Cancelled"];

export function formatCost(value, currency = "MYR") {
	try {
		return new Intl.NumberFormat(undefined, {
			style: "currency",
			currency,
			minimumFractionDigits: 2,
		}).format(value || 0);
	} catch {
		return `${currency} ${Number(value || 0).toFixed(2)}`;
	}
}

export function flt(value) {
	return parseFloat(value) || 0;
}

export function parseFrappeError(data) {
	if (!data) return "Request failed";

	if (typeof data === "string") return data;

	if (data._server_messages) {
		try {
			const messages = JSON.parse(data._server_messages);
			const parsed = JSON.parse(messages[0]);
			if (parsed.message) return parsed.message;
		} catch {
			// Fall through to message field.
		}
	}

	return data.message || "Request failed";
}

export function unwrapFrappeMessage(data) {
	if (data && typeof data === "object" && data.message !== undefined) {
		return data.message;
	}
	return data;
}

export async function uploadAudioFile(file) {
	const formData = new FormData();
	formData.append("file", file);
	formData.append("is_private", "1");

	const csrfToken = window.csrf_token && window.csrf_token !== "None" ? window.csrf_token : "";

	const response = await fetch("/api/method/audio_stem.api.separation.upload_audio_file", {
		method: "POST",
		headers: {
			"X-Frappe-CSRF-Token": csrfToken,
		},
		credentials: "include",
		body: formData,
	});

	const data = await response.json();
	if (!response.ok || data.exc) {
		throw new Error(parseFrappeError(data) || "Upload failed");
	}

	return data.message;
}

export function getEstimatedCost(job, costPerSecond) {
	if (!job?.duration_seconds) return null;
	return job.estimated_cost_usd ?? job.duration_seconds * (costPerSecond || 0);
}

export function isStartDisabled({ job, jobName, starting, enabled, credit, costPerSecond }) {
	if (!jobName || starting || enabled === 0) return true;
	if (!job) return true;
	if (!job.can_start || job.is_active) return true;

	const cost = getEstimatedCost(job, costPerSecond);
	if (
		credit?.enabled &&
		!credit.error &&
		job.duration_seconds &&
		credit.available_balance !== null &&
		credit.available_balance !== undefined &&
		flt(cost) > flt(credit.available_balance)
	) {
		return true;
	}

	return false;
}

export function getStartBlockedReason({ job, credit, costPerSecond }) {
	if (!job) return "";
	if (job.is_active) return "";

	const cost = getEstimatedCost(job, costPerSecond);
	if (
		credit?.enabled &&
		!credit.error &&
		job.duration_seconds &&
		credit.available_balance !== null &&
		credit.available_balance !== undefined &&
		flt(cost) > flt(credit.available_balance)
	) {
		return "Insufficient available credits for this separation job.";
	}

	if (!job.can_start) {
		return job.start_blocked_reason || "Start is not available for this job.";
	}

	return "";
}

export { ACTIVE_STATUSES, TERMINAL_STATUSES };

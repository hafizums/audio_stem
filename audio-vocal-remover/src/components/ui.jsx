import {
	ACTIVE_STATUSES,
	formatCost,
	formatDateTime,
	getJobStatusMessage,
	getStatusBadgeClass,
} from "../utils";

export function StatusBadge({ status }) {
	return (
		<span
			className={`inline-flex rounded-full px-2 py-0.5 text-xs font-medium ${getStatusBadgeClass(
				status
			)}`}
		>
			{status}
		</span>
	);
}

export function StatusPill({ settings, credit }) {
	const pills = [];
	if (settings?.enabled) {
		pills.push({ label: "Enabled", className: "bg-green-100 text-green-800" });
	} else {
		pills.push({ label: "Disabled", className: "bg-red-100 text-red-800" });
	}
	if (settings?.pilot_mode_enabled) {
		pills.push({ label: "Pilot", className: "bg-amber-100 text-amber-800" });
	}
	if (settings?.credit_management_enabled) {
		pills.push({ label: "Credits", className: "bg-indigo-100 text-indigo-800" });
	}
	if (settings?.daily_usage?.limits_enabled) {
		pills.push({ label: "Limits", className: "bg-purple-100 text-purple-800" });
	}
	if (!pills.length) return null;
	return (
		<div className="flex flex-wrap gap-1.5">
			{pills.map((pill) => (
				<span
					key={pill.label}
					className={`inline-flex rounded-full px-2.5 py-0.5 text-xs font-medium ${pill.className}`}
				>
					{pill.label}
				</span>
			))}
		</div>
	);
}

export function EmptyJobState() {
	return (
		<div className="rounded-xl border border-dashed border-gray-300 bg-white px-6 py-10 text-center shadow-sm">
			<p className="text-base font-semibold text-gray-900">No job selected</p>
			<p className="mt-1 text-sm text-gray-500">
				Upload an audio file to begin. Your workflow will guide you step by step.
			</p>
		</div>
	);
}

export function LockedTab({ title, message }) {
	return (
		<div className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-8 text-center">
			<p className="text-base font-semibold text-amber-900">{title}</p>
			<p className="mt-2 text-sm text-amber-800">{message}</p>
		</div>
	);
}

export function ProcessingNotice({ job, statusMessage }) {
	return (
		<div className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-3 text-sm text-blue-800">
			<p className="font-medium">{statusMessage || getJobStatusMessage(job, {})}</p>
			<p className="mt-1 text-xs text-blue-700">
				You can leave this page and come back later.
			</p>
		</div>
	);
}

export function SafeErrorNotice({ message }) {
	if (!message) return null;
	return (
		<div className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
			{message}
		</div>
	);
}

export function CompactDetailRow({ label, children }) {
	return (
		<div className="flex items-baseline justify-between gap-3 text-sm">
			<dt className="text-gray-500">{label}</dt>
			<dd className="text-right font-medium text-gray-900">{children}</dd>
		</div>
	);
}

export function Card({ title, children, className = "", actions = null }) {
	return (
		<section
			className={`rounded-xl border border-gray-200 bg-white p-5 shadow-sm ${className}`}
		>
			{(title || actions) && (
				<div className="mb-3 flex items-center justify-between gap-2">
					{title && (
						<h3 className="text-sm font-semibold text-gray-900">{title}</h3>
					)}
					{actions && <div className="flex items-center gap-2">{actions}</div>}
				</div>
			)}
			{children}
		</section>
	);
}

export function PrimaryButton({ children, disabled, onClick, className = "" }) {
	return (
		<button
			type="button"
			disabled={disabled}
			onClick={onClick}
			className={`rounded-md bg-purple-600 px-4 py-2 text-sm font-semibold text-white hover:bg-purple-700 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
		>
			{children}
		</button>
	);
}

export function SecondaryButton({ children, disabled, onClick, className = "" }) {
	return (
		<button
			type="button"
			disabled={disabled}
			onClick={onClick}
			className={`rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50 ${className}`}
		>
			{children}
		</button>
	);
}

export { ACTIVE_STATUSES, formatCost, formatDateTime };

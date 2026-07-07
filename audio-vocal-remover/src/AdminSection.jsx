import { useFrappeGetCall } from "frappe-react-sdk";
import AdminChecklist from "./AdminChecklist";
import { formatDateTime, parseFrappeError, unwrapFrappeMessage } from "./utils";

function HealthCard({ title, children }) {
	return (
		<div className="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm">
			<p className="font-medium text-gray-900">{title}</p>
			<div className="mt-2 space-y-1 text-gray-700">{children}</div>
		</div>
	);
}

export default function AdminSection() {
	const { data: checklistResponse, error: checklistError, isLoading } = useFrappeGetCall(
		"audio_stem.api.admin.get_configuration_checklist"
	);
	const { data: queueResponse } = useFrappeGetCall("audio_stem.api.admin.get_queue_health");
	const { data: providerResponse } = useFrappeGetCall("audio_stem.api.admin.get_provider_health");

	const checklistItems = unwrapFrappeMessage(checklistResponse) || [];
	const queue = unwrapFrappeMessage(queueResponse);
	const provider = unwrapFrappeMessage(providerResponse);

	return (
		<div className="space-y-4">
			<section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
				<h2 className="mb-3 text-base font-semibold text-gray-900">Admin: Queue Health</h2>
				{queue ? (
					<div className="grid gap-3 sm:grid-cols-2">
						<HealthCard title="Queue summary">
							<p>Active: {queue.active_jobs_count}</p>
							<p>Queued: {queue.queued_jobs_count}</p>
							<p>Uploading: {queue.uploading_jobs_count}</p>
							<p>Processing: {queue.processing_jobs_count}</p>
							<p>Recent failures (24h): {queue.recent_failures_count}</p>
						</HealthCard>
						<HealthCard title="Guidance">
							<p>{queue.worker_guidance_message}</p>
							{queue.oldest_active_job_age_minutes != null && (
								<p>Oldest active job: {queue.oldest_active_job_age_minutes} min</p>
							)}
						</HealthCard>
					</div>
				) : (
					<p className="text-sm text-gray-500">Loading queue health...</p>
				)}
				{queue?.stuck_jobs?.length > 0 && (
					<div className="mt-4 overflow-x-auto">
						<table className="min-w-full border border-gray-200 text-sm">
							<thead className="bg-gray-50">
								<tr>
									<th className="border-b px-2 py-2 text-left">Job</th>
									<th className="border-b px-2 py-2 text-left">User</th>
									<th className="border-b px-2 py-2 text-left">Status</th>
									<th className="border-b px-2 py-2 text-left">Age (min)</th>
									<th className="border-b px-2 py-2 text-left">Created</th>
								</tr>
							</thead>
							<tbody>
								{queue.stuck_jobs.map((row) => (
									<tr key={row.name}>
										<td className="border-b px-2 py-2">{row.name}</td>
										<td className="border-b px-2 py-2">{row.user}</td>
										<td className="border-b px-2 py-2">{row.status}</td>
										<td className="border-b px-2 py-2">{row.age_minutes}</td>
										<td className="border-b px-2 py-2">{formatDateTime(row.creation)}</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				)}
			</section>

			<section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
				<h2 className="mb-3 text-base font-semibold text-gray-900">Admin: Provider Health</h2>
				{provider ? (
					<div className="rounded-md border border-gray-200 bg-gray-50 p-3 text-sm text-gray-700">
						<p>
							Status: <strong>{provider.status}</strong>
						</p>
						<p>{provider.message}</p>
						<p>Completed (24h): {provider.completed_count}</p>
						<p>Failed (24h): {provider.failed_count}</p>
						{provider.success_rate != null && <p>Success rate: {provider.success_rate}</p>}
					</div>
				) : (
					<p className="text-sm text-gray-500">Loading provider health...</p>
				)}
			</section>

			<section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
				<h2 className="mb-3 text-base font-semibold text-gray-900">Admin: Configuration Checklist</h2>
				<p className="mb-3 text-sm text-gray-600">System Manager view. No secrets are shown here.</p>
				<AdminChecklist
					items={checklistItems}
					loading={isLoading}
					error={
						checklistError
							? parseFrappeError(checklistError) || "Unable to load checklist."
							: null
					}
				/>
			</section>
		</div>
	);
}

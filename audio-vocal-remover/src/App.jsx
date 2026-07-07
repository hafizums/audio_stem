import { useCallback, useEffect, useRef, useState } from "react";
import {
	FrappeProvider,
	useFrappeAuth,
	useFrappeFileUpload,
	useFrappeGetCall,
	useFrappePostCall,
} from "frappe-react-sdk";
import {
	ACTIVE_STATUSES,
	TERMINAL_STATUSES,
	formatCost,
	getEstimatedCost,
	getStartBlockedReason,
	isStartDisabled,
	parseFrappeError,
	unwrapFrappeMessage,
} from "./utils";

function Section({ title, children }) {
	return (
		<section className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm">
			<h2 className="mb-3 text-base font-semibold text-gray-900">{title}</h2>
			{children}
		</section>
	);
}

function LoginPrompt() {
	return (
		<div className="mx-auto max-w-lg rounded-lg border border-amber-200 bg-amber-50 p-6 text-center">
			<h1 className="text-xl font-semibold text-gray-900">Audio Vocal Remover</h1>
			<p className="mt-2 text-sm text-gray-600">
				Please log in to upload audio and run separation jobs.
			</p>
			<a
				href="/login?redirect-to=/audio-vocal-remover"
				className="mt-4 inline-block rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800"
			>
				Log in
			</a>
		</div>
	);
}

function AuthenticatedApp({ currentUser }) {
	const [jobName, setJobName] = useState(null);
	const [job, setJob] = useState(null);
	const [starting, setStarting] = useState(false);
	const [uploading, setUploading] = useState(false);
	const [error, setError] = useState(null);
	const pollRef = useRef(null);

	const { data: settingsResponse } = useFrappeGetCall(
		"audio_stem.api.separation.get_page_settings"
	);
	const { data: creditBalanceResponse, mutate: refreshCredit } = useFrappeGetCall(
		"audio_stem.api.separation.get_my_credit_balance"
	);
	const { data: recentJobsResponse, mutate: refreshRecent } = useFrappeGetCall(
		"audio_stem.api.separation.get_recent_jobs",
		{ limit: 10 }
	);
	const settings = unwrapFrappeMessage(settingsResponse);
	const creditBalance = unwrapFrappeMessage(creditBalanceResponse);
	const recentJobs = unwrapFrappeMessage(recentJobsResponse) || [];

	const { upload: uploadFile } = useFrappeFileUpload();

	const { call: createJob } = useFrappePostCall(
		"audio_stem.api.separation.create_job_from_file"
	);
	const { call: startSeparation } = useFrappePostCall(
		"audio_stem.api.separation.start_separation"
	);
	const { call: getJobStatus } = useFrappePostCall(
		"audio_stem.api.separation.get_job_status"
	);

	const displayCurrency = job?.display_currency || settings?.display_currency || "MYR";
	const costPerSecond = settings?.cost_per_second_usd || 0;
	const credit = creditBalance || { enabled: false };
	const estimatedCost = getEstimatedCost(job, costPerSecond);
	const startDisabled = isStartDisabled({
		job,
		jobName,
		starting,
		enabled: settings?.enabled,
		credit,
		costPerSecond,
	});
	const startBlockedReason = getStartBlockedReason({ job, credit, costPerSecond });

	const stopPolling = useCallback(() => {
		if (pollRef.current) {
			clearInterval(pollRef.current);
			pollRef.current = null;
		}
	}, []);

	const fetchJobStatus = useCallback(async () => {
		if (!jobName) return;
		const nextJob = unwrapFrappeMessage(await getJobStatus({ job_name: jobName }));
		setJob(nextJob);
		if (!ACTIVE_STATUSES.includes(nextJob?.status)) {
			stopPolling();
		}
		if (TERMINAL_STATUSES.includes(nextJob?.status)) {
			refreshRecent();
			refreshCredit();
		}
	}, [getJobStatus, jobName, refreshCredit, refreshRecent, stopPolling]);

	const startPolling = useCallback(() => {
		stopPolling();
		fetchJobStatus();
		pollRef.current = setInterval(fetchJobStatus, 3000);
	}, [fetchJobStatus, stopPolling]);

	useEffect(() => {
		if (job && ACTIVE_STATUSES.includes(job.status)) {
			startPolling();
		} else {
			stopPolling();
		}
		return () => stopPolling();
	}, [job?.status, jobName, startPolling, stopPolling]);

	const handleFileChange = async (event) => {
		const file = event.target.files?.[0];
		event.target.value = "";
		if (!file) return;

		setError(null);
		setUploading(true);
		try {
			const uploaded = await uploadFile(
				file,
				{ isPrivate: true },
				undefined,
				"audio_stem.api.separation.upload_audio_file"
			);
			const created = unwrapFrappeMessage(
				await createJob({ file_url: uploaded.file_url })
			);
			if (!created?.name) {
				throw new Error("Job was created but no job ID was returned.");
			}
			setJobName(created.name);
			setJob(created);
			await refreshRecent();
			await refreshCredit();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to upload audio");
		} finally {
			setUploading(false);
		}
	};

	const handleStart = async () => {
		if (!jobName || starting) return;
		if (settings?.enabled === 0) {
			setError("Audio separation is disabled in Audio Separation Settings.");
			return;
		}

		setStarting(true);
		setError(null);
		try {
			const result = unwrapFrappeMessage(
				await startSeparation({ job_name: jobName })
			);
			if (result) {
				setJob((prev) => ({ ...prev, ...result }));
			}
			await fetchJobStatus();
			await refreshCredit();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to start separation");
		} finally {
			setStarting(false);
		}
	};

	const loadJob = async (name) => {
		setJobName(name);
		setError(null);
		const nextJob = unwrapFrappeMessage(await getJobStatus({ job_name: name }));
		setJob(nextJob);
	};

	return (
		<div className="min-h-screen bg-gray-100">
			<header className="border-b border-gray-200 bg-white">
				<div className="mx-auto flex max-w-4xl items-center justify-between px-4 py-4">
					<div>
						<h1 className="text-lg font-bold text-gray-900">Audio Vocal Remover</h1>
						<p className="text-sm text-gray-500">Separate vocals and instrumentals with WaveSpeed</p>
					</div>
					<div className="text-right text-sm text-gray-600">
						<p>{currentUser}</p>
						<a href="/login?redirect-to=/audio-vocal-remover" className="text-blue-600 hover:underline">
							Switch account
						</a>
					</div>
				</div>
			</header>

			<main className="mx-auto max-w-4xl space-y-4 p-4">
				{error && (
					<div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
						{error}
					</div>
				)}

				<Section title="Upload Audio">
					{jobName && (
						<p className="mb-3 text-sm text-gray-500">
							Job created. Upload another file to start over.
						</p>
					)}
					<label className="inline-flex cursor-pointer items-center rounded-md bg-gray-900 px-4 py-2 text-sm font-medium text-white hover:bg-gray-800">
						{uploading ? "Uploading..." : "Upload Audio File"}
						<input
							type="file"
							className="hidden"
							accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac"
							disabled={uploading}
							onChange={handleFileChange}
						/>
					</label>
				</Section>

				<Section title="Limits">
					<p className="text-sm text-gray-600">
						{settings
							? `Max file size: ${settings.max_file_size_mb} MB. Max duration: ${settings.max_audio_duration_seconds} seconds.`
							: "Loading limits..."}
					</p>
				</Section>

				<Section title="Cost Estimate">
					{!job ? (
						<p className="text-sm text-gray-500">Upload an audio file to see the estimate.</p>
					) : job.duration_seconds ? (
						<p className="text-sm text-gray-700">
							Duration: <strong>{job.duration_seconds}s</strong>
							<br />
							Estimated provider cost:{" "}
							<strong>{formatCost(estimatedCost, displayCurrency)}</strong>
						</p>
					) : (
						<p className="text-sm text-gray-500">Cost will be calculated after upload.</p>
					)}
				</Section>

				<Section title="Credits">
					{!credit.enabled ? (
						<p className="text-sm text-gray-500">Credit management is not enabled.</p>
					) : credit.error ? (
						<p className="text-sm text-red-600">{credit.error}</p>
					) : (
						<div className="space-y-1 text-sm text-gray-700">
							<p>
								Credit type: <strong>{credit.credit_type}</strong>
							</p>
							<p>
								Current balance:{" "}
								<strong>{formatCost(credit.current_balance, displayCurrency)}</strong>
							</p>
							<p>
								Reserved balance:{" "}
								<strong>{formatCost(credit.reserved_balance, displayCurrency)}</strong>
							</p>
							<p>
								Available balance:{" "}
								<strong>{formatCost(credit.available_balance, displayCurrency)}</strong>
							</p>
							<p>
								{job?.duration_seconds ? (
									<>
										Estimated job cost:{" "}
										<strong>{formatCost(estimatedCost, displayCurrency)}</strong>
									</>
								) : (
									"Estimated job cost will appear after upload."
								)}
							</p>
						</div>
					)}
				</Section>

				<Section title="Start Separation">
					<button
						type="button"
						disabled={startDisabled}
						onClick={handleStart}
						className="rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50"
					>
						{starting ? "Starting..." : "Start Separation"}
					</button>
					{startBlockedReason && (
						<p className="mt-2 text-sm text-gray-600">{startBlockedReason}</p>
					)}
				</Section>

				<Section title="Job Status">
					<p className="font-medium text-gray-900">
						{job ? (
							<>
								Job <strong>{job.name}</strong> — Status: {job.status}
							</>
						) : (
							"No active job"
						)}
					</p>
					{job?.error_message &&
						(job.status === "Failed" || job.status === "Completed") && (
							<p className="mt-2 text-sm text-red-600">{job.error_message}</p>
						)}
				</Section>

				<Section title="Original Audio">
					{job?.original_file ? (
						<audio controls preload="none" src={job.original_file} className="w-full" />
					) : (
						<p className="text-sm text-gray-500">Original audio not available.</p>
					)}
				</Section>

				<Section title="Vocal Output">
					{job?.vocal_output_url ? (
						<>
							<audio controls preload="none" src={job.vocal_output_url} className="w-full" />
							<a
								href={job.vocal_output_url}
								className="mt-2 inline-block text-sm text-blue-600 hover:underline"
								download
							>
								Download Vocal
							</a>
						</>
					) : (
						<p className="text-sm text-gray-500">Vocal output not available yet.</p>
					)}
				</Section>

				<Section title="Instrumental Output">
					{job?.instrumental_output_url ? (
						<>
							<audio
								controls
								preload="none"
								src={job.instrumental_output_url}
								className="w-full"
							/>
							<a
								href={job.instrumental_output_url}
								className="mt-2 inline-block text-sm text-blue-600 hover:underline"
								download
							>
								Download Instrumental
							</a>
						</>
					) : (
						<p className="text-sm text-gray-500">Instrumental output not available yet.</p>
					)}
				</Section>

				<Section title="Recent Jobs">
					{!recentJobs?.length ? (
						<p className="text-sm text-gray-500">No jobs yet.</p>
					) : (
						<div className="overflow-x-auto">
							<table className="min-w-full border border-gray-200 text-sm">
								<thead className="bg-gray-50">
									<tr>
										<th className="border-b px-3 py-2 text-left">Job</th>
										<th className="border-b px-3 py-2 text-left">Status</th>
										<th className="border-b px-3 py-2 text-left">Duration (s)</th>
									</tr>
								</thead>
								<tbody>
									{recentJobs.map((row) => (
										<tr
											key={row.name}
											className="cursor-pointer hover:bg-gray-50"
											onClick={() => loadJob(row.name)}
										>
											<td className="border-b px-3 py-2 text-blue-600">{row.name}</td>
											<td className="border-b px-3 py-2">{row.status}</td>
											<td className="border-b px-3 py-2">{row.duration_seconds || ""}</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					)}
				</Section>
			</main>
		</div>
	);
}

function AppContent() {
	const { currentUser, isLoading: authLoading } = useFrappeAuth();

	if (authLoading) {
		return <div className="p-8 text-center text-gray-500">Loading...</div>;
	}

	if (!currentUser || currentUser === "Guest") {
		return (
			<div className="flex min-h-screen items-center justify-center p-6">
				<LoginPrompt />
			</div>
		);
	}

	return <AuthenticatedApp currentUser={currentUser} />;
}

export default function App() {
	return (
		<FrappeProvider>
			<AppContent />
		</FrappeProvider>
	);
}

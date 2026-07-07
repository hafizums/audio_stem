import { useCallback, useEffect, useRef, useState } from "react";
import {
	FrappeProvider,
	useFrappeAuth,
	useFrappeFileUpload,
	useFrappeGetCall,
	useFrappePostCall,
} from "frappe-react-sdk";
import AdminSection from "./AdminSection";
import JobDetailPanel, { StatusBadge } from "./JobDetailPanel";
import {
	ACTIVE_STATUSES,
	TERMINAL_STATUSES,
	formatCost,
	formatDateTime,
	getEstimatedCost,
	getJobStatusMessage,
	getStartBlockedReason,
	getUploadErrorMessage,
	isStartDisabled,
	parseFrappeError,
	unwrapFrappeMessage,
} from "./utils";

function Section({ title, children, className = "" }) {
	return (
		<section
			className={`rounded-lg border border-gray-200 bg-white p-4 shadow-sm ${className}`}
		>
			{title && <h2 className="mb-3 text-base font-semibold text-gray-900">{title}</h2>}
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
	const [retrying, setRetrying] = useState(false);
	const [zipping, setZipping] = useState(false);
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
	const { call: retryFailedJob } = useFrappePostCall(
		"audio_stem.api.separation.retry_failed_job"
	);
	const { call: createJobZip } = useFrappePostCall(
		"audio_stem.api.separation.create_job_zip"
	);
	const { call: getJobDetail } = useFrappePostCall(
		"audio_stem.api.separation.get_job_detail"
	);

	const displayCurrency = job?.display_currency || settings?.display_currency || "MYR";
	const costPerSecond = settings?.cost_per_second_usd || 0;
	const credit = settings?.credit_management_enabled
		? creditBalance || { enabled: true }
		: { enabled: false };
	const estimatedCost = getEstimatedCost(job, costPerSecond);
	const startDisabled = isStartDisabled({
		job,
		jobName,
		starting,
		enabled: settings?.enabled,
		credit,
		costPerSecond,
	});
	const startBlockedReason = getStartBlockedReason({ job, credit, costPerSecond, settings });
	const statusMessage = getJobStatusMessage(job, { starting, retrying, zipping });

	const stopPolling = useCallback(() => {
		if (pollRef.current) {
			clearInterval(pollRef.current);
			pollRef.current = null;
		}
	}, []);

	const fetchJobDetail = useCallback(async () => {
		if (!jobName) return;
		const nextJob = unwrapFrappeMessage(await getJobDetail({ job_name: jobName }));
		setJob(nextJob);
		if (!ACTIVE_STATUSES.includes(nextJob?.status)) {
			stopPolling();
		}
		if (TERMINAL_STATUSES.includes(nextJob?.status)) {
			refreshRecent();
			refreshCredit();
		}
	}, [getJobDetail, jobName, refreshCredit, refreshRecent, stopPolling]);

	const startPolling = useCallback(() => {
		stopPolling();
		fetchJobDetail();
		pollRef.current = setInterval(fetchJobDetail, 3000);
	}, [fetchJobDetail, stopPolling]);

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
			setError(getUploadErrorMessage(err, settings));
		} finally {
			setUploading(false);
		}
	};

	const handleStart = async () => {
		if (!jobName || starting || job?.is_active) return;
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
			await fetchJobDetail();
			await refreshCredit();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to start separation");
			await refreshCredit();
		} finally {
			setStarting(false);
		}
	};

	const handleRetry = async (name) => {
		if (retrying) return;
		setRetrying(true);
		setError(null);
		try {
			const result = unwrapFrappeMessage(await retryFailedJob({ job_name: name }));
			setJobName(name);
			if (result) {
				setJob((prev) => ({ ...(prev?.name === name ? prev : {}), ...result }));
			}
			await fetchJobDetail();
			await refreshRecent();
			await refreshCredit();
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to retry job");
		} finally {
			setRetrying(false);
		}
	};

	const handleZip = async (name) => {
		if (zipping) return;
		setZipping(true);
		setError(null);
		try {
			const result = unwrapFrappeMessage(await createJobZip({ job_name: name }));
			if (result?.zip_file) {
				window.open(result.zip_file, "_blank", "noopener,noreferrer");
			}
			if (jobName === name) {
				await fetchJobDetail();
			}
		} catch (err) {
			setError(parseFrappeError(err) || err.message || "Failed to create ZIP file");
		} finally {
			setZipping(false);
		}
	};

	const loadJob = async (name) => {
		setJobName(name);
		setError(null);
		const nextJob = unwrapFrappeMessage(await getJobDetail({ job_name: name }));
		setJob(nextJob);
	};

	return (
		<div className="min-h-screen bg-gray-100">
			<header className="border-b border-gray-200 bg-white">
				<div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
					<div>
						<h1 className="text-lg font-bold text-gray-900">Audio Vocal Remover</h1>
						<p className="text-sm text-gray-500">
							Upload an audio file and generate separate vocal and instrumental tracks.
						</p>
					</div>
					<div className="text-sm text-gray-600 sm:text-right">
						<p>{currentUser}</p>
						<a
							href="/login?redirect-to=/audio-vocal-remover"
							className="text-blue-600 hover:underline"
						>
							Switch account
						</a>
					</div>
				</div>
			</header>

			<main className="mx-auto max-w-6xl space-y-4 p-4">
				{error && (
					<div className="rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
						{error}
					</div>
				)}

				<Section title="">
					<p className="text-sm text-gray-700">
						Upload an audio file and generate separate vocal and instrumental tracks using
						WaveSpeed.
					</p>
					<ul className="mt-3 list-inside list-disc space-y-1 text-sm text-gray-600">
						<li>
							Accepted types: {settings?.accepted_file_types || "MP3, WAV, M4A, FLAC, OGG, AAC"}
						</li>
						<li>
							Max file size: {settings?.max_file_size_mb ?? "—"} MB. Max duration:{" "}
							{settings?.max_audio_duration_seconds ?? "—"} seconds.
						</li>
						{settings?.credit_management_enabled && (
							<li>Credits are required before starting separation when credit integration is enabled.</li>
						)}
					</ul>
				</Section>

				<div className="grid gap-4 lg:grid-cols-2">
					<div className="space-y-4">
						<Section title="Upload Audio">
							{uploading && (
								<p className="mb-3 text-sm text-blue-700">Uploading and creating job...</p>
							)}
							{jobName && !uploading && (
								<p className="mb-3 text-sm text-gray-500">
									Job <strong>{jobName}</strong> created. Upload another file to start over.
								</p>
							)}
							<label className="flex w-full cursor-pointer items-center justify-center rounded-lg border-2 border-dashed border-gray-300 bg-gray-50 px-4 py-8 text-sm font-medium text-gray-700 hover:border-gray-400 hover:bg-gray-100 sm:py-10">
								{uploading ? "Uploading..." : "Tap or click to upload audio"}
								<input
									type="file"
									className="hidden"
									accept="audio/*,.mp3,.wav,.m4a,.flac,.ogg,.aac"
									disabled={uploading}
									onChange={handleFileChange}
								/>
							</label>
						</Section>

						{job && job.status === "Draft" && (
							<Section title="Cost Estimate">
								{job.duration_seconds ? (
									<p className="text-sm text-gray-700">
										Duration: <strong>{job.duration_seconds}s</strong>
										<br />
										Estimated provider cost:{" "}
										<strong>{formatCost(estimatedCost, displayCurrency)}</strong>
									</p>
								) : (
									<p className="text-sm text-amber-700">
										Audio duration is unknown. Separation cannot be started until duration is
										available.
									</p>
								)}
							</Section>
						)}

						{settings?.credit_management_enabled && (
							<Section title="Credits">
								{credit.error ? (
									<p className="text-sm text-red-600">
										Credit integration is unavailable: {credit.error}
									</p>
								) : (
									<div className="space-y-1 text-sm text-gray-700">
										<p>
											Available balance:{" "}
											<strong>{formatCost(credit.available_balance, displayCurrency)}</strong>
										</p>
										{job?.duration_seconds && (
											<p>
												Estimated job cost:{" "}
												<strong>{formatCost(estimatedCost, displayCurrency)}</strong>
											</p>
										)}
									</div>
								)}
							</Section>
						)}

						{job && job.status === "Draft" && (
							<Section title="Start Separation">
								<button
									type="button"
									disabled={startDisabled || job?.is_active}
									onClick={handleStart}
									className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
								>
									{starting ? "Starting..." : "Start Separation"}
								</button>
								{startBlockedReason && (
									<p className="mt-2 text-sm text-gray-600">{startBlockedReason}</p>
								)}
							</Section>
						)}
					</div>

					<JobDetailPanel
						job={job}
						settings={settings}
						displayCurrency={displayCurrency}
						statusMessage={statusMessage}
						onRetry={handleRetry}
						onZip={handleZip}
						retrying={retrying}
						zipping={zipping}
					/>
				</div>

				<Section title="Recent Jobs">
					{!recentJobs?.length ? (
						<div className="rounded-md border border-dashed border-gray-200 bg-gray-50 px-4 py-8 text-center">
							<p className="text-sm font-medium text-gray-700">No jobs yet</p>
							<p className="mt-1 text-sm text-gray-500">
								Upload your first audio file to create a separation job.
							</p>
						</div>
					) : (
						<div className="-mx-4 overflow-x-auto sm:mx-0">
							<table className="min-w-full border border-gray-200 text-sm">
								<thead className="bg-gray-50">
									<tr>
										<th className="border-b px-2 py-2 text-left sm:px-3">Job</th>
										<th className="hidden border-b px-2 py-2 text-left sm:table-cell sm:px-3">
											File
										</th>
										<th className="border-b px-2 py-2 text-left sm:px-3">Status</th>
										{settings?.credit_management_enabled && (
											<th className="hidden border-b px-2 py-2 text-left md:table-cell md:px-3">
												Credit
											</th>
										)}
										<th className="hidden border-b px-2 py-2 text-left lg:table-cell lg:px-3">
											Duration
										</th>
										<th className="border-b px-2 py-2 text-left sm:px-3">Actions</th>
									</tr>
								</thead>
								<tbody>
									{recentJobs.map((row) => (
										<tr
											key={row.name}
											className={`hover:bg-gray-50 ${jobName === row.name ? "bg-blue-50" : ""}`}
										>
											<td className="border-b px-2 py-2 font-medium text-gray-900 sm:px-3">
												{row.name}
												<p className="text-xs text-gray-500 sm:hidden">
													{row.original_filename || "—"}
												</p>
											</td>
											<td className="hidden border-b px-2 py-2 text-gray-600 sm:table-cell sm:px-3">
												{row.original_filename || "—"}
											</td>
											<td className="border-b px-2 py-2 sm:px-3">
												<StatusBadge status={row.status} />
												{row.error_summary && (
													<p className="mt-1 text-xs text-red-600">{row.error_summary}</p>
												)}
											</td>
											{settings?.credit_management_enabled && (
												<td className="hidden border-b px-2 py-2 md:table-cell md:px-3">
													{row.credit_status || "—"}
												</td>
											)}
											<td className="hidden border-b px-2 py-2 lg:table-cell lg:px-3">
												{row.duration_seconds ? `${row.duration_seconds}s` : "—"}
											</td>
											<td className="border-b px-2 py-2 sm:px-3">
												<div className="flex flex-wrap gap-2">
													<button
														type="button"
														onClick={() => loadJob(row.name)}
														className="text-blue-600 hover:underline"
													>
														Open
													</button>
													{row.can_retry && (
														<button
															type="button"
															disabled={retrying}
															onClick={() => handleRetry(row.name)}
															className="text-amber-700 hover:underline disabled:opacity-50"
														>
															Retry
														</button>
													)}
													{row.can_zip && (
														<button
															type="button"
															disabled={zipping}
															onClick={() => handleZip(row.name)}
															className="text-green-700 hover:underline disabled:opacity-50"
														>
															ZIP
														</button>
													)}
												</div>
											</td>
										</tr>
									))}
								</tbody>
							</table>
						</div>
					)}
				</Section>

				{settings?.is_system_manager && <AdminSection />}
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

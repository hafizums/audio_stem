import { useEffect, useState } from "react";
import TranscriptEditor from "./TranscriptEditor";
import KaraokeStyleCard from "./KaraokeStyleCard";
import WorkflowTabs, { useWorkflowTab, getRecommendedNextAction } from "./components/WorkflowTabs";
import {
	ACTIVE_STATUSES,
	Card,
	CompactDetailRow,
	EmptyJobState,
	LockedTab,
	PrimaryButton,
	ProcessingNotice,
	SafeErrorNotice,
	SecondaryButton,
	StatusBadge,
} from "./components/ui";
import { formatCost, formatDateTime } from "./utils";

function AudioPreview({ label, src }) {
	if (!src) {
		return (
			<div className="rounded-lg border border-gray-100 bg-gray-50 px-3 py-3 text-sm text-gray-500">
				<p className="font-medium text-gray-700">{label}</p>
				<p className="mt-1 text-xs">Not available yet.</p>
			</div>
		);
	}
	return (
		<div className="rounded-lg border border-gray-200 bg-white px-3 py-3">
			<p className="mb-2 text-sm font-medium text-gray-800">{label}</p>
			<audio controls preload="none" src={src} className="w-full max-w-full" />
			<a href={src} className="mt-2 inline-block text-sm text-blue-600 hover:underline" download>
				Download
			</a>
		</div>
	);
}

function VideoPreviewCard({ src, label = "Karaoke video" }) {
	const [expanded, setExpanded] = useState(false);
	if (!src) return null;
	return (
		<div className="rounded-lg border border-gray-200 bg-white p-3">
			<div className="flex items-center justify-between">
				<p className="text-sm font-medium text-gray-800">{label}</p>
				<button
					type="button"
					onClick={() => setExpanded((v) => !v)}
					className="text-xs text-blue-600 hover:underline"
				>
					{expanded ? "Collapse" : "Expand"}
				</button>
			</div>
			<video
				controls
				preload="none"
				src={src}
				className={`mt-2 w-full rounded-md transition-all ${
					expanded ? "max-h-[70vh]" : "max-h-48"
				}`}
			/>
			<a href={src} className="mt-2 inline-block text-sm text-blue-600 hover:underline" download>
				Download MP4
			</a>
		</div>
	);
}

function SeparateTab({
	job,
	settings,
	displayCurrency,
	statusMessage,
	estimatedCost,
	startDisabled,
	startBlockedReason,
	starting,
	cancelling,
	retrying,
	zipping,
	onStart,
	onCancel,
	onRetry,
	onZip,
}) {
	const isProgress = ACTIVE_STATUSES.includes(job.status);
	return (
		<div className="space-y-4">
			{isProgress && <ProcessingNotice job={job} statusMessage={statusMessage} />}
			{job.status === "Completed" && (
				<div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
					Separation completed. Move to <strong>Transcribe</strong> to extract lyrics.
				</div>
			)}
			{job.status === "Failed" && (
				<SafeErrorNotice message={job.error_message || "Separation failed."} />
			)}
			{job.status === "Cancelled" && (
				<div className="rounded-lg border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-700">
					This job was cancelled.
					{job.cancel_reason ? ` Reason: ${job.cancel_reason}` : ""}
				</div>
			)}

			<Card title="Tracks">
				<div className="grid gap-3 sm:grid-cols-3">
					<AudioPreview label="Original" src={job.original_file} />
					<AudioPreview label="Vocal" src={job.vocal_output_url || job.vocal_file} />
					<AudioPreview
						label="Instrumental"
						src={job.instrumental_output_url || job.instrumental_file}
					/>
				</div>
			</Card>

			{job.status === "Draft" && (
				<Card title="Start separation">
					{job.duration_seconds ? (
						<dl className="mb-3 space-y-1 text-sm">
							<CompactDetailRow label="Duration">{job.duration_seconds}s</CompactDetailRow>
							<CompactDetailRow label="Estimated cost">
								{formatCost(estimatedCost, displayCurrency)}
							</CompactDetailRow>
						</dl>
					) : (
						<p className="mb-3 text-sm text-amber-700">
							Audio duration is unknown. Separation cannot start until duration is available.
						</p>
					)}
					<PrimaryButton disabled={startDisabled || job.is_active} onClick={onStart}>
						{starting ? "Starting..." : "Start Separation"}
					</PrimaryButton>
					{startBlockedReason && (
						<p className="mt-2 text-sm text-gray-600">{startBlockedReason}</p>
					)}
				</Card>
			)}

			<div className="flex flex-wrap gap-2">
				{job.can_cancel && (
					<SecondaryButton disabled={cancelling} onClick={() => onCancel(job.name)}>
						{cancelling ? "Cancelling..." : "Cancel"}
					</SecondaryButton>
				)}
				{job.status === "Failed" && job.can_retry && (
					<SecondaryButton
						disabled={retrying || job.is_active}
						onClick={() => onRetry(job.name)}
					>
						{retrying ? "Retrying..." : "Retry"}
					</SecondaryButton>
				)}
				{job.can_zip && (
					<SecondaryButton disabled={zipping} onClick={() => onZip(job.name)}>
						{zipping ? "Creating ZIP..." : "ZIP"}
					</SecondaryButton>
				)}
			</div>
		</div>
	);
}

function TranscribeTab({
	job,
	settings,
	displayCurrency,
	transcribing,
	onTranscription,
}) {
	const [transcriptionSource, setTranscriptionSource] = useState("Vocal");
	const [transcriptionLanguage, setTranscriptionLanguage] = useState(
		job.default_transcription_language || settings?.default_transcription_language || ""
	);

	const openaiEnabled = job.openai_enabled || settings?.openai_enabled;
	const vocalTranscriptionBlocked =
		job.is_active || job.status !== "Completed" || !job.has_vocal;
	const transcriptionDisabled =
		!openaiEnabled ||
		!job.can_start_transcription ||
		transcribing ||
		job.is_transcription_active ||
		(transcriptionSource === "Vocal" && vocalTranscriptionBlocked);

	if (job.status !== "Completed") {
		return (
			<LockedTab
				title="Complete audio separation first"
				message="Transcription is available once your vocal and instrumental tracks are ready."
			/>
		);
	}

	if (!openaiEnabled) {
		return (
			<LockedTab
				title="Transcription is disabled"
				message="OpenAI transcription is not enabled for this site."
			/>
		);
	}

	return (
		<div className="space-y-4">
			{job.is_transcription_active && (
				<ProcessingNotice
					job={job}
					statusMessage="Transcribing lyrics with Whisper. This usually takes a minute."
				/>
			)}
			{job.transcription_status === "Completed" && (
				<div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
					Transcription complete. Move to <strong>Edit Lyrics</strong> to fix any mistakes.
				</div>
			)}
			{job.transcription_error && <SafeErrorNotice message={job.transcription_error} />}

			<Card title="Run transcription">
				<div className="mb-3 flex flex-wrap items-center gap-2">
					<StatusBadge status={job.transcription_status || "Not Started"} />
					{job.transcription_cost_usd > 0 && (
						<span className="text-xs text-gray-500">
							Cost: {formatCost(job.transcription_cost_usd, displayCurrency)}
						</span>
					)}
				</div>
				<div className="grid gap-3 sm:grid-cols-2">
					<label className="text-sm text-gray-700">
						Source
						<select
							className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
							value={transcriptionSource}
							onChange={(e) => setTranscriptionSource(e.target.value)}
							disabled={transcribing || job.is_transcription_active}
						>
							<option value="Vocal">Vocal (recommended)</option>
							<option value="Original">Original audio</option>
						</select>
					</label>
					<label className="text-sm text-gray-700">
						Language (optional)
						<input
							type="text"
							className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
							value={transcriptionLanguage}
							onChange={(e) => setTranscriptionLanguage(e.target.value)}
							placeholder="Auto-detect"
							disabled={transcribing || job.is_transcription_active}
						/>
					</label>
				</div>
				<div className="mt-3">
					<PrimaryButton
						disabled={transcriptionDisabled}
						onClick={() =>
							onTranscription(job.name, transcriptionSource, transcriptionLanguage)
						}
					>
						{transcribing || job.is_transcription_active
							? "Transcribing..."
							: "Start Transcription"}
					</PrimaryButton>
				</div>
				{job.transcription_blocked_reason && transcriptionDisabled && (
					<p className="mt-2 text-sm text-gray-600">{job.transcription_blocked_reason}</p>
				)}
			</Card>

			{job.transcript_text && (
				<Card title="Lyrics preview">
					<p className="max-h-40 overflow-y-auto whitespace-pre-wrap text-sm text-gray-800">
						{job.transcript_text}
					</p>
				</Card>
			)}
		</div>
	);
}

function EditLyricsTab({
	job,
	settings,
	karaokeRendering,
	onLoadTranscript,
	onSaveTranscript,
	onApproveTranscript,
	onResetTranscript,
	onRegenerateSubtitles,
	onDownloadManualTranscript,
	onJobUpdated,
}) {
	if (job.transcription_status !== "Completed") {
		return (
			<LockedTab
				title="Run transcription before editing lyrics"
				message="Transcribe your track first, then come back here to correct any lyrics."
			/>
		);
	}
	return (
		<TranscriptEditor
			job={job}
			settings={settings}
			disabled={!job.can_edit_transcript || job.is_karaoke_active || karaokeRendering}
			onLoad={onLoadTranscript}
			onSave={onSaveTranscript}
			onApprove={onApproveTranscript}
			onReset={onResetTranscript}
			onRegenerate={onRegenerateSubtitles}
			onDownloadManual={onDownloadManualTranscript}
			onJobUpdated={onJobUpdated}
		/>
	);
}

function KaraokeTab({
	job,
	settings,
	jobKaraokeStyle,
	karaokeRendering,
	onKaraoke,
	onUploadKaraokeBackground,
	onClearKaraokeBackground,
	onSaveJobKaraokeStyle,
	onResetJobKaraokeStyle,
	onSaveSiteKaraokeStyle,
	savingJobKaraokeStyle,
	savingSiteKaraokeStyle,
}) {
	const [karaokeSourceMode, setKaraokeSourceMode] = useState(job.karaoke_source_mode || "Auto");
	const [karaokeAudioMode, setKaraokeAudioMode] = useState(job.karaoke_audio_mode || "Auto");

	useEffect(() => {
		setKaraokeSourceMode(job.karaoke_source_mode || "Auto");
		setKaraokeAudioMode(job.karaoke_audio_mode || "Auto");
	}, [job.karaoke_source_mode, job.karaoke_audio_mode, job.name]);

	const selectedKaraokeAudioLabel =
		karaokeAudioMode === "Original"
			? "Original song"
			: karaokeAudioMode === "Instrumental"
				? "Instrumental track"
				: settings?.karaoke_include_instrumental_audio === false
					? "Original song"
					: "Instrumental track";

	const selectedKaraokeSourceLabel =
		karaokeSourceMode === "Original Whisper"
			? "Original Whisper"
			: karaokeSourceMode === "Manual Corrected"
				? "Manual Corrected"
				: job.manual_transcript_is_approved && job.has_manual_transcript
					? "Manual Corrected"
					: "Original Whisper";
	const karaokeNeedsRegenerate =
		job.karaoke_status === "Completed" &&
		job.karaoke_rendered_transcript_source_label &&
		job.karaoke_rendered_transcript_source_label !== selectedKaraokeSourceLabel;

	const karaokeEnabled = job.karaoke_enabled || settings?.karaoke_enabled;
	const karaokeDisabled =
		!karaokeEnabled || !job.can_start_karaoke || karaokeRendering || job.is_karaoke_active;
	const backgroundUploadDisabled =
		karaokeRendering || job.is_karaoke_active || !job.can_upload_karaoke_background;
	const karaokeInfoMessage =
		job.karaoke_status === "Completed" &&
		job.karaoke_error &&
		(job.karaoke_error.includes("Video render is disabled") ||
			job.karaoke_error.includes("Video render failed"));

	if (job.transcription_status !== "Completed") {
		return (
			<LockedTab
				title="Run transcription before karaoke"
				message="Karaoke subtitles use your transcript. Transcribe first, then generate karaoke."
			/>
		);
	}

	if (!karaokeEnabled) {
		return (
			<LockedTab
				title="Karaoke is disabled"
				message="Karaoke subtitle generation is not enabled for this site."
			/>
		);
	}

	return (
		<div className="space-y-4">
			{job.is_karaoke_active && (
				<ProcessingNotice
					job={job}
					statusMessage="Generating karaoke subtitles. This may take a minute."
				/>
			)}
			{job.karaoke_status === "Completed" && !job.karaoke_error && (
				<div className="rounded-lg border border-green-200 bg-green-50 px-4 py-3 text-sm text-green-800">
					Karaoke ready. Move to <strong>Downloads</strong> to grab your files.
				</div>
			)}

			<KaraokeStyleCard
				jobStyle={jobKaraokeStyle}
				settings={settings}
				disabled={karaokeRendering || job.is_karaoke_active}
				onSaveJobStyle={onSaveJobKaraokeStyle}
				onResetJobStyle={onResetJobKaraokeStyle}
				onSaveSiteStyle={onSaveSiteKaraokeStyle}
				savingJobStyle={savingJobKaraokeStyle}
				savingSiteStyle={savingSiteKaraokeStyle}
				canEditSiteStyle={!!settings?.is_system_manager}
			/>

			<Card title="Generate karaoke">
				<div className="mb-3 flex flex-wrap items-center gap-2">
					<StatusBadge status={job.karaoke_status || "Not Started"} />
				</div>
				<label className="block text-sm text-gray-700">
					Karaoke source
					<select
						className="mt-1 w-full max-w-xs rounded-md border border-gray-300 px-2 py-1.5 text-sm"
						value={karaokeSourceMode}
						onChange={(event) => setKaraokeSourceMode(event.target.value)}
						disabled={karaokeDisabled}
					>
						<option value="Auto">Auto</option>
						<option value="Original Whisper">Original Whisper</option>
						<option value="Manual Corrected">Manual Corrected</option>
					</select>
				</label>
				<label className="mt-3 block text-sm text-gray-700">
					Video audio
					<select
						className="mt-1 w-full max-w-xs rounded-md border border-gray-300 px-2 py-1.5 text-sm"
						value={karaokeAudioMode}
						onChange={(event) => setKaraokeAudioMode(event.target.value)}
						disabled={karaokeDisabled}
					>
						<option value="Auto">Auto (site default)</option>
						<option value="Instrumental">Instrumental track</option>
						<option value="Original">Original song</option>
					</select>
				</label>
				<p className="mt-1 text-xs text-gray-500">
					Next render audio: {selectedKaraokeAudioLabel}
				</p>
				{job.karaoke_rendered_transcript_source_label && (
					<p className="mt-2 text-xs text-gray-500">
						Last render used: {job.karaoke_rendered_transcript_source_label}
					</p>
				)}
				<p className="mt-1 text-xs text-gray-500">
					Next render will use: {selectedKaraokeSourceLabel}
				</p>
				{karaokeNeedsRegenerate && (
					<p className="mt-2 text-sm text-amber-700">
						The current karaoke output does not match the selected source. Click regenerate to
						update.
					</p>
				)}
				<div className="mt-3">
					<PrimaryButton
						disabled={karaokeDisabled}
						onClick={() =>
							onKaraoke(job.name, {
								karaokeSourceMode,
								karaokeAudioMode,
							})
						}
					>
						{karaokeRendering || job.is_karaoke_active
							? "Generating..."
							: job.karaoke_status === "Completed"
								? "Regenerate Karaoke"
								: "Generate Karaoke"}
					</PrimaryButton>
				</div>
				{job.karaoke_blocked_reason && karaokeDisabled && (
					<p className="mt-2 text-sm text-gray-600">{job.karaoke_blocked_reason}</p>
				)}
				{karaokeInfoMessage && (
					<p className="mt-2 text-sm text-gray-600">{job.karaoke_error}</p>
				)}
				{job.karaoke_error && !karaokeInfoMessage && (
					<SafeErrorNotice message={job.karaoke_error} />
				)}
			</Card>

			<Card
				title="Background video"
				actions={
					<span className="text-xs text-gray-500">
						{settings?.karaoke_video_render_enabled ? "MP4 render on" : "MP4 render off"}
					</span>
				}
			>
				<p className="text-xs text-gray-500">
					ASS subtitles work without a background video. A background is only used when MP4
					rendering is enabled.
				</p>
				<p className="mt-2 text-sm text-gray-700">
					Current background:{" "}
					<span className="font-medium">
						{job.karaoke_background_source || "Generated Color"}
					</span>
					{job.karaoke_background_filename ? (
						<span className="text-gray-500"> ({job.karaoke_background_filename})</span>
					) : null}
				</p>
				{job.karaoke_background_note && (
					<p className="mt-1 text-sm text-amber-700">{job.karaoke_background_note}</p>
				)}
				{!job.can_upload_karaoke_background && (
					<p className="mt-1 text-xs text-gray-500">
						User background uploads are disabled. Only the site default or generated color
						background may be used.
					</p>
				)}
				<div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-center">
					<label className="block text-sm text-gray-700">
						<span className="sr-only">Upload background video</span>
						<input
							type="file"
							accept="video/mp4,video/webm,video/quicktime,video/x-matroska,.mp4,.mov,.webm,.mkv"
							disabled={backgroundUploadDisabled || !settings?.karaoke_video_render_enabled}
							onChange={(event) => {
								const file = event.target.files?.[0];
								event.target.value = "";
								if (file) onUploadKaraokeBackground(job.name, file);
							}}
							className="block w-full max-w-md text-sm text-gray-700 file:mr-3 file:rounded-md file:border-0 file:bg-purple-50 file:px-3 file:py-1.5 file:text-sm file:font-medium file:text-purple-700 hover:file:bg-purple-100 disabled:cursor-not-allowed disabled:opacity-50"
						/>
					</label>
					{(job.has_karaoke_background_video || job.karaoke_background_video_file) && (
						<SecondaryButton
							disabled={backgroundUploadDisabled}
							onClick={() => onClearKaraokeBackground(job.name)}
						>
							Clear
						</SecondaryButton>
					)}
				</div>
				{backgroundUploadDisabled && job.is_karaoke_active && (
					<p className="mt-2 text-xs text-gray-500">
						Background changes are disabled while karaoke rendering is active.
					</p>
				)}
				{!settings?.karaoke_video_render_enabled && (
					<p className="mt-2 text-xs text-gray-500">
						Enable MP4 rendering in settings to use a custom background video.
					</p>
				)}
			</Card>

			{job.karaoke_video_file && (
				<VideoPreviewCard src={job.karaoke_video_file} label="Karaoke MP4 preview" />
			)}
		</div>
	);
}

function DownloadLink({ label, onClick }) {
	return (
		<button
			type="button"
			onClick={onClick}
			className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-left text-sm text-gray-800 hover:border-purple-300 hover:bg-purple-50"
		>
			{label}
		</button>
	);
}

function DownloadsTab({
	job,
	onDownloadTranscript,
	onDownloadManualTranscript,
	onZip,
	zipping,
}) {
	const items = [
		{
			group: "Audio stems",
			links: [
				{
					label: "Vocal track",
					available: !!(job.vocal_output_url || job.vocal_file),
					href: job.vocal_output_url || job.vocal_file,
				},
				{
					label: "Instrumental track",
					available: !!(job.instrumental_output_url || job.instrumental_file),
					href: job.instrumental_output_url || job.instrumental_file,
				},
				{ label: "Original audio", available: !!job.original_file, href: job.original_file },
			],
		},
		{
			group: "Whisper transcript",
			links: [
				{
					label: "Transcript JSON",
					available: !!(job.has_current_transcript_json ?? job.has_transcript_json),
					onClick: () => onDownloadTranscript(job.name, "json"),
				},
				{
					label: "Transcript SRT",
					available: !!(job.has_current_transcript_srt ?? job.has_transcript_srt),
					onClick: () => onDownloadTranscript(job.name, "srt"),
				},
				{
					label: "Transcript VTT",
					available: !!(job.has_current_transcript_vtt ?? job.has_transcript_vtt),
					onClick: () => onDownloadTranscript(job.name, "vtt"),
				},
			],
		},
		{
			group: "Manual transcript",
			links: [
				{
					label: "Manual JSON",
					available: !!(job.has_current_manual_transcript ?? job.has_manual_transcript),
					onClick: () => onDownloadManualTranscript(job.name, "json"),
				},
				{
					label: "Manual SRT",
					available: !!(job.has_manual_transcript_srt || job.manual_transcript_srt_file),
					onClick: () => onDownloadManualTranscript(job.name, "srt"),
				},
				{
					label: "Manual VTT",
					available: !!(job.has_manual_transcript_vtt || job.manual_transcript_vtt_file),
					onClick: () => onDownloadManualTranscript(job.name, "vtt"),
				},
			],
		},
		{
			group: "Karaoke",
			links: [
				{
					label: "Karaoke ASS",
					available: !!(job.has_current_karaoke_ass ?? (job.has_karaoke_ass || job.karaoke_ass_file)),
					href: job.has_current_karaoke_ass ? job.karaoke_ass_file : null,
				},
				{
					label: "Karaoke MP4",
					available: !!(job.has_current_karaoke_video && job.karaoke_video_file),
					href: job.has_current_karaoke_video ? job.karaoke_video_file : null,
				},
			],
		},
	];

	const anyAvailable = items.some((g) => g.links.some((l) => l.available));
	if (!anyAvailable) {
		return (
			<LockedTab
				title="Generate karaoke subtitle first"
				message="Once separation, transcription, and karaoke are done, your downloads will appear here."
			/>
		);
	}

	return (
		<div className="space-y-4">
			{job.can_zip && (
				<Card title="All-in-one">
					<PrimaryButton disabled={zipping} onClick={() => onZip(job.name)}>
						{zipping ? "Creating ZIP..." : "Download ZIP"}
					</PrimaryButton>
				</Card>
			)}
			{items.map((group) => {
				const available = group.links.filter((l) => l.available);
				if (!available.length) return null;
				return (
					<Card key={group.group} title={group.group}>
						<div className="grid gap-2 sm:grid-cols-2">
							{group.links.map((link) => {
								if (!link.available) {
									return (
										<p
											key={link.label}
											className="rounded-md border border-dashed border-gray-100 bg-gray-50 px-3 py-2 text-left text-xs text-gray-400"
										>
											{link.label} — not available
										</p>
									);
								}
								if (link.href) {
									return (
										<a
											key={link.label}
											href={link.href}
											download
											className="w-full rounded-md border border-gray-200 bg-white px-3 py-2 text-left text-sm text-gray-800 hover:border-purple-300 hover:bg-purple-50"
										>
											{link.label}
										</a>
									);
								}
								return (
									<DownloadLink key={link.label} label={link.label} onClick={link.onClick} />
								);
							})}
						</div>
					</Card>
				);
			})}
		</div>
	);
}

export default function JobDetailPanel({
	job,
	settings,
	displayCurrency,
	statusMessage,
	onRetry,
	onZip,
	onCancel,
	onTranscription,
	onKaraoke,
	onUploadKaraokeBackground,
	onClearKaraokeBackground,
	onDownloadTranscript,
	onLoadTranscript,
	onSaveTranscript,
	onApproveTranscript,
	onResetTranscript,
	onRegenerateSubtitles,
	onDownloadManualTranscript,
	onJobUpdated,
	onStart,
	jobKaraokeStyle,
	onSaveJobKaraokeStyle,
	onResetJobKaraokeStyle,
	onSaveSiteKaraokeStyle,
	savingJobKaraokeStyle,
	savingSiteKaraokeStyle,
	starting,
	retrying,
	zipping,
	cancelling,
	transcribing,
	karaokeRendering,
	estimatedCost,
	startDisabled,
	startBlockedReason,
}) {
	const [activeTab, setActiveTab] = useWorkflowTab(job);

	if (!job) {
		return <EmptyJobState />;
	}

	const recommendedNext = getRecommendedNextAction(job);
	const isProgress = ACTIVE_STATUSES.includes(job.status);
	const tabProps = { job, settings, displayCurrency, statusMessage };

	return (
		<div className="space-y-4">
			<section className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div className="min-w-0">
						<p className="truncate text-lg font-semibold text-gray-900">
							{job.original_filename || job.name}
						</p>
						<p className="mt-0.5 text-xs text-gray-500">{job.name}</p>
					</div>
					<StatusBadge status={job.status} />
				</div>

				<dl className="mt-4 grid gap-2 sm:grid-cols-2">
					<CompactDetailRow label="Duration">
						{job.duration_seconds ? `${job.duration_seconds}s` : "—"}
					</CompactDetailRow>
					<CompactDetailRow label="Estimated cost">
						{formatCost(estimatedCost, displayCurrency)}
					</CompactDetailRow>
					<CompactDetailRow label="Created">
						{formatDateTime(job.creation) || "—"}
					</CompactDetailRow>
					{settings?.credit_management_enabled && (
						<CompactDetailRow label="Credit status">
							<span className="inline-flex items-center gap-2">
								{job.credit_status || "—"}
								{job.reconciliation_required && (
									<span className="rounded-full bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-800">
										Reconciliation
									</span>
								)}
							</span>
						</CompactDetailRow>
					)}
				</dl>

				<div className="mt-4 rounded-md border border-purple-100 bg-purple-50 px-3 py-2 text-sm text-purple-900">
					<span className="font-medium">Next step:</span> {recommendedNext}
				</div>

				{(isProgress || job.is_transcription_active || job.is_karaoke_active) && (
					<div className="mt-3">
						<ProcessingNotice job={job} statusMessage={statusMessage} />
					</div>
				)}

				{job.downstream_assets_stale && (
					<div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900">
						<p className="font-medium">Downstream assets are stale</p>
						<p className="mt-1 text-xs text-amber-800">
							{job.downstream_stale_reason ||
								"Audio separation was regenerated. Re-run transcription and karaoke."}
						</p>
					</div>
				)}

				{settings?.credit_management_enabled && (job.reconciliation_required || job.credit_error) && (
					<div className="mt-3 space-y-2">
						{job.reconciliation_required && (
							<div className="rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900">
								<p className="font-medium">Credit reconciliation required</p>
								<p className="mt-1 text-xs text-amber-800">
									Separation completed but credits may not have been consumed.
									{settings?.is_system_manager
										? " Open Admin Tools → Credit Reconciliation to retry."
										: " Please contact an administrator."}
								</p>
							</div>
						)}
						<SafeErrorNotice message={job.credit_error} />
					</div>
				)}

				<div className="mt-4 flex flex-wrap gap-2">
					{job.status === "Draft" && (
						<PrimaryButton disabled={startDisabled || job.is_active} onClick={onStart}>
							{starting ? "Starting..." : "Start Separation"}
						</PrimaryButton>
					)}
					{job.can_cancel && (
						<SecondaryButton disabled={cancelling} onClick={() => onCancel(job.name)}>
							{cancelling ? "Cancelling..." : "Cancel"}
						</SecondaryButton>
					)}
					{job.status === "Failed" && job.can_retry && (
						<SecondaryButton
							disabled={retrying || job.is_active}
							onClick={() => onRetry(job.name)}
						>
							{retrying ? "Retrying..." : "Retry"}
						</SecondaryButton>
					)}
					{job.can_zip && (
						<SecondaryButton disabled={zipping} onClick={() => onZip(job.name)}>
							{zipping ? "Creating ZIP..." : "ZIP"}
						</SecondaryButton>
					)}
					<a
						href={`/app/audio-separation-job/${job.name}`}
						target="_blank"
						rel="noopener noreferrer"
						className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
					>
						Open in Desk
					</a>
				</div>
			</section>

			<WorkflowTabs activeTab={activeTab} onChange={setActiveTab} />

			<div>
				{activeTab === "separate" && (
					<SeparateTab
						{...tabProps}
						estimatedCost={estimatedCost}
						startDisabled={startDisabled}
						startBlockedReason={startBlockedReason}
						starting={starting}
						cancelling={cancelling}
						retrying={retrying}
						zipping={zipping}
						onStart={onStart}
						onCancel={onCancel}
						onRetry={onRetry}
						onZip={onZip}
					/>
				)}
				{activeTab === "transcribe" && (
					<TranscribeTab
						{...tabProps}
						transcribing={transcribing}
						onTranscription={onTranscription}
						onDownloadTranscript={onDownloadTranscript}
					/>
				)}
				{activeTab === "lyrics" && (
					<EditLyricsTab
						{...tabProps}
						karaokeRendering={karaokeRendering}
						onLoadTranscript={onLoadTranscript}
						onSaveTranscript={onSaveTranscript}
						onApproveTranscript={onApproveTranscript}
						onResetTranscript={onResetTranscript}
						onRegenerateSubtitles={onRegenerateSubtitles}
						onDownloadManualTranscript={onDownloadManualTranscript}
						onJobUpdated={onJobUpdated}
					/>
				)}
				{activeTab === "karaoke" && (
					<KaraokeTab
						{...tabProps}
						jobKaraokeStyle={jobKaraokeStyle}
						karaokeRendering={karaokeRendering}
						onKaraoke={onKaraoke}
						onUploadKaraokeBackground={onUploadKaraokeBackground}
						onClearKaraokeBackground={onClearKaraokeBackground}
						onSaveJobKaraokeStyle={onSaveJobKaraokeStyle}
						onResetJobKaraokeStyle={onResetJobKaraokeStyle}
						onSaveSiteKaraokeStyle={onSaveSiteKaraokeStyle}
						savingJobKaraokeStyle={savingJobKaraokeStyle}
						savingSiteKaraokeStyle={savingSiteKaraokeStyle}
					/>
				)}
				{activeTab === "downloads" && (
					<DownloadsTab
						job={job}
						zipping={zipping}
						onDownloadTranscript={onDownloadTranscript}
						onDownloadManualTranscript={onDownloadManualTranscript}
						onZip={onZip}
					/>
				)}
			</div>
		</div>
	);
}

export { StatusBadge };

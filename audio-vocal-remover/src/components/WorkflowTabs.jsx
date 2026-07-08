import { useEffect, useState } from "react";

const TAB_DEFS = [
	{ key: "separate", label: "1. Separate" },
	{ key: "transcribe", label: "2. Transcribe" },
	{ key: "lyrics", label: "3. Edit Lyrics" },
	{ key: "karaoke", label: "4. Karaoke" },
	{ key: "downloads", label: "5. Downloads" },
];

export function getRecommendedTab(job) {
	if (!job) return "separate";
	if (job.status !== "Completed") return "separate";
	if ((job.transcription_status || "Not Started") !== "Completed") return "transcribe";
	if (job.karaoke_status === "Completed") return "downloads";
	if (job.can_edit_transcript) return "lyrics";
	if (job.transcription_status === "Completed") return "karaoke";
	return "transcribe";
}

export function getRecommendedNextAction(job) {
	if (!job) return "Upload an audio file to begin.";
	if (job.status === "Draft") return "Review the estimate and start separation.";
	if (job.status === "Queued") return "Your job is waiting in the queue.";
	if (job.status === "Uploading") return "Uploading audio to the processing service.";
	if (job.status === "Processing") return "Separating vocals and instrumentals.";
	if (job.status === "Failed") return job.error_message || "Separation failed. Retry when ready.";
	if (job.status === "Cancelled") return "This job was cancelled.";
	if ((job.transcription_status || "Not Started") !== "Completed")
		return "Run transcription to extract lyrics.";
	if ((job.karaoke_status || "Not Started") !== "Completed")
		return "Generate karaoke subtitles from your transcript.";
	return "Download your final karaoke outputs.";
}

export default function WorkflowTabs({ activeTab, onChange, tabs }) {
	const list = tabs || TAB_DEFS;
	return (
		<div className="overflow-x-auto rounded-lg border border-gray-200 bg-white p-1 shadow-sm">
			<nav className="flex min-w-max gap-1" aria-label="Workflow steps">
				{list.map((tab) => {
					const isActive = tab.key === activeTab;
					return (
						<button
							key={tab.key}
							type="button"
							onClick={() => onChange(tab.key)}
							className={`whitespace-nowrap rounded-md px-3 py-2 text-sm font-medium transition ${
								isActive
									? "bg-purple-600 text-white shadow-sm"
									: "text-gray-700 hover:bg-gray-100"
							}`}
							aria-current={isActive ? "page" : undefined}
						>
							{tab.label}
						</button>
					);
				})}
			</nav>
		</div>
	);
}

export function useWorkflowTab(job) {
	const recommended = getRecommendedTab(job);
	const [activeTab, setActiveTab] = useState(recommended);

	useEffect(() => {
		setActiveTab(recommended);
		// Only reset when job changes, not every render.
		// eslint-disable-next-line react-hooks/exhaustive-deps
	}, [job?.name]);

	return [activeTab, setActiveTab, recommended];
}

export { TAB_DEFS };

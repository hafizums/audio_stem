import { useEffect, useMemo, useState } from "react";
import { Card, SecondaryButton } from "./components/ui";

const PRESET_OPTIONS = [
	{ value: "default_1080p", label: "Default" },
	{ value: "mobile_1080x1920", label: "Mobile" },
	{ value: "classic_center_3line", label: "Classic Center Karaoke" },
];

const FONT_OPTIONS = [
	{ value: "Helvetica", label: "Helvetica" },
	{ value: "Arial Narrow", label: "Arial Narrow" },
	{ value: "Bebas Neue", label: "Bebas Neue" },
];

const PREVIEW_FONT_FAMILIES = {
	Helvetica: "Helvetica, Arial, sans-serif",
	"Arial Narrow": '"Arial Narrow", Arial, sans-serif',
	"Bebas Neue": '"Bebas Neue", Impact, sans-serif',
};

const TIMING_GRANULARITY_OPTIONS = [
	{ value: "word", label: "Word" },
	{ value: "syllable", label: "Syllable" },
	{ value: "character", label: "Character" },
];

const TIMING_GRANULARITY_HELP = {
	word: "Best for normal lyrics.",
	syllable: 'Better for karaoke singing like "Lalalili Lili Lila".',
	character: "Experimental for very hard timing.",
};

const STYLE_FIELDS = [
	"karaoke_style_preset",
	"karaoke_timing_granularity",
	"karaoke_font_size",
	"karaoke_visible_lines",
	"karaoke_center_y_percent",
	"karaoke_line_gap",
	"karaoke_primary_color",
	"karaoke_highlight_color",
	"karaoke_previous_line_color",
	"karaoke_next_line_color",
	"karaoke_outline_color",
	"karaoke_outline",
	"karaoke_shadow",
];

const OVERRIDE_FIELD_MAP = {
	karaoke_style_preset: "karaoke_style_preset_override",
	karaoke_timing_granularity: "karaoke_timing_granularity_override",
	karaoke_visible_lines: "karaoke_visible_lines_override",
	karaoke_center_y_percent: "karaoke_center_y_percent_override",
	karaoke_line_gap: "karaoke_line_gap_override",
	karaoke_font_size: "karaoke_font_size_override",
	karaoke_primary_color: "karaoke_primary_color_override",
	karaoke_highlight_color: "karaoke_highlight_color_override",
	karaoke_previous_line_color: "karaoke_previous_line_color_override",
	karaoke_next_line_color: "karaoke_next_line_color_override",
	karaoke_outline_color: "karaoke_outline_color_override",
	karaoke_shadow: "karaoke_shadow_override",
	karaoke_outline: "karaoke_outline_override",
};

function ClassicStylePreview({
	highlightColor = "#3366FF",
	previousColor = "#3366FF",
	nextColor = "#FFFFFF",
	fontName = "Helvetica",
}) {
	const fontFamily = PREVIEW_FONT_FAMILIES[fontName] || PREVIEW_FONT_FAMILIES.Helvetica;
	return (
		<div
			className="rounded-lg border border-gray-800 bg-black px-4 py-6 text-center"
			style={{ fontFamily }}
		>
			<p className="text-lg font-semibold" style={{ color: previousColor }}>
				I found a love for me
			</p>
			<p className="mt-3 text-lg font-semibold text-white">
				<span style={{ color: highlightColor }}>Dar</span>
				ling, just dive right in
			</p>
			<p className="mt-3 text-lg font-semibold" style={{ color: nextColor }}>
				and follow my lead
			</p>
		</div>
	);
}

function styleFromEffective(effective = {}) {
	return {
		karaoke_style_preset: effective.karaoke_style_preset || "default_1080p",
		karaoke_timing_granularity: effective.karaoke_timing_granularity || "word",
		karaoke_font_name: effective.karaoke_font_name || "Helvetica",
		karaoke_font_size: effective.karaoke_font_size ?? 64,
		karaoke_highlight_color: effective.karaoke_highlight_color || "#3366FF",
		karaoke_primary_color: effective.karaoke_primary_color || "#FFFFFF",
		karaoke_previous_line_color: effective.karaoke_previous_line_color || "#3366FF",
		karaoke_next_line_color: effective.karaoke_next_line_color || "#FFFFFF",
		karaoke_outline_color: effective.karaoke_outline_color || "#000000",
		karaoke_center_y_percent: effective.karaoke_center_y_percent ?? 50,
		karaoke_line_gap: effective.karaoke_line_gap ?? 90,
		karaoke_visible_lines: effective.karaoke_visible_lines ?? 3,
		karaoke_shadow: effective.karaoke_shadow ?? 1,
		karaoke_outline: effective.karaoke_outline ?? 3,
	};
}

function overridePayloadFromState(overrideEnabled, styleState) {
	const payload = {
		karaoke_style_override_enabled: overrideEnabled ? 1 : 0,
	};
	if (!overrideEnabled) {
		return payload;
	}
	for (const [styleField, overrideField] of Object.entries(OVERRIDE_FIELD_MAP)) {
		payload[overrideField] = styleState[styleField];
	}
	return payload;
}

export default function KaraokeStyleCard({
	jobStyle,
	settings,
	disabled,
	onSaveJobStyle,
	onResetJobStyle,
	onSaveSiteStyle,
	savingJobStyle,
	savingSiteStyle,
	canEditSiteStyle,
}) {
	const [overrideEnabled, setOverrideEnabled] = useState(
		!!jobStyle?.override_enabled
	);
	const [styleState, setStyleState] = useState(() =>
		styleFromEffective(jobStyle?.effective_style || settings)
	);
	const [siteStyleState, setSiteStyleState] = useState(() =>
		styleFromEffective(jobStyle?.global_style || settings)
	);

	useEffect(() => {
		setOverrideEnabled(!!jobStyle?.override_enabled);
		setStyleState(styleFromEffective(jobStyle?.effective_style || settings));
		setSiteStyleState(styleFromEffective(jobStyle?.global_style || settings));
	}, [jobStyle, settings]);

	const previewStyle = useMemo(() => {
		if (overrideEnabled) {
			return styleState;
		}
		return siteStyleState;
	}, [overrideEnabled, siteStyleState, styleState]);

	const isClassic = previewStyle.karaoke_style_preset === "classic_center_3line";
	const inputDisabled = disabled || (!overrideEnabled && !canEditSiteStyle);
	const jobInputDisabled = disabled || !overrideEnabled;

	const update = (field, value) => {
		setStyleState((current) => ({ ...current, [field]: value }));
	};

	const updateSite = (field, value) => {
		setSiteStyleState((current) => ({ ...current, [field]: value }));
	};

	const activeState = overrideEnabled ? styleState : siteStyleState;
	const activeUpdate = overrideEnabled ? update : updateSite;

	return (
		<Card title="Subtitle style">
			<p className="mb-3 text-xs text-gray-500">
				Customize karaoke subtitle appearance per job without changing site-wide defaults.
				Regenerate karaoke after saving style changes.
			</p>

			<label className="mb-3 flex items-center gap-2 text-sm text-gray-700">
				<input
					type="checkbox"
					checked={overrideEnabled}
					onChange={(event) => setOverrideEnabled(event.target.checked)}
					disabled={disabled}
				/>
				Use custom style for this job
			</label>

			{!overrideEnabled ? (
				<p className="mb-3 text-xs text-gray-500">Using site default karaoke style.</p>
			) : (
				<p className="mb-3 text-xs text-amber-700">
					Editing per-job style overrides. Site defaults remain unchanged.
				</p>
			)}

			{jobStyle?.rendered_style_source && (
				<p className="mb-3 text-xs text-gray-500">
					Last generated ASS/MP4 used: <strong>{jobStyle.rendered_style_source}</strong>
				</p>
			)}

			{jobStyle?.needs_regenerate_for_style && (
				<p className="mb-3 text-sm text-amber-700">
					Regenerate karaoke subtitle/video to apply style changes.
				</p>
			)}

			<div className="grid gap-3 sm:grid-cols-2">
				<label className="text-sm text-gray-700 sm:col-span-2">
					Highlight timing
					<select
						className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
						value={activeState.karaoke_timing_granularity || "word"}
						onChange={(e) => activeUpdate("karaoke_timing_granularity", e.target.value)}
						disabled={overrideEnabled ? jobInputDisabled : inputDisabled}
					>
						{TIMING_GRANULARITY_OPTIONS.map((option) => (
							<option key={option.value} value={option.value}>
								{option.label}
							</option>
						))}
					</select>
					<span className="mt-1 block text-xs text-gray-500">
						{TIMING_GRANULARITY_HELP[activeState.karaoke_timing_granularity || "word"]}
					</span>
					{jobStyle?.needs_regenerate_for_style &&
						(jobStyle?.has_karaoke_ass || jobStyle?.has_karaoke_video) && (
							<span className="mt-1 block text-xs text-amber-700">
								Regenerate karaoke subtitle/video to apply timing changes.
							</span>
						)}
				</label>
				<label className="text-sm text-gray-700">
					Style preset
					<select
						className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
						value={activeState.karaoke_style_preset}
						onChange={(e) => activeUpdate("karaoke_style_preset", e.target.value)}
						disabled={overrideEnabled ? jobInputDisabled : inputDisabled}
					>
						{PRESET_OPTIONS.map((option) => (
							<option key={option.value} value={option.value}>
								{option.label}
							</option>
						))}
					</select>
				</label>
				<label className="text-sm text-gray-700">
					Font
					<select
						className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
						value={activeState.karaoke_font_name}
						disabled
						title="Font is configured site-wide"
					>
						{FONT_OPTIONS.map((option) => (
							<option key={option.value} value={option.value}>
								{option.label}
							</option>
						))}
					</select>
				</label>
				<label className="text-sm text-gray-700">
					Font size
					<input
						type="number"
						min={24}
						max={120}
						className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
						value={activeState.karaoke_font_size}
						onChange={(e) => activeUpdate("karaoke_font_size", Number(e.target.value))}
						disabled={(overrideEnabled ? jobInputDisabled : inputDisabled) || !isClassic}
					/>
				</label>
				<label className="text-sm text-gray-700">
					Visible lines
					<input
						type="number"
						min={1}
						max={5}
						className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
						value={activeState.karaoke_visible_lines}
						onChange={(e) => activeUpdate("karaoke_visible_lines", Number(e.target.value))}
						disabled={(overrideEnabled ? jobInputDisabled : inputDisabled) || !isClassic}
					/>
				</label>
				<label className="text-sm text-gray-700">
					Center Y position (%)
					<input
						type="number"
						min={10}
						max={90}
						step={1}
						className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
						value={activeState.karaoke_center_y_percent}
						onChange={(e) => activeUpdate("karaoke_center_y_percent", Number(e.target.value))}
						disabled={(overrideEnabled ? jobInputDisabled : inputDisabled) || !isClassic}
					/>
				</label>
				<label className="text-sm text-gray-700">
					Line gap
					<input
						type="number"
						min={20}
						max={300}
						className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
						value={activeState.karaoke_line_gap}
						onChange={(e) => activeUpdate("karaoke_line_gap", Number(e.target.value))}
						disabled={(overrideEnabled ? jobInputDisabled : inputDisabled) || !isClassic}
					/>
				</label>
				<label className="text-sm text-gray-700">
					Text color
					<input
						type="color"
						className="mt-1 h-10 w-full rounded-md border border-gray-300"
						value={activeState.karaoke_primary_color}
						onChange={(e) => activeUpdate("karaoke_primary_color", e.target.value)}
						disabled={(overrideEnabled ? jobInputDisabled : inputDisabled) || !isClassic}
					/>
				</label>
				<label className="text-sm text-gray-700">
					Highlight color
					<input
						type="color"
						className="mt-1 h-10 w-full rounded-md border border-gray-300"
						value={activeState.karaoke_highlight_color}
						onChange={(e) => activeUpdate("karaoke_highlight_color", e.target.value)}
						disabled={(overrideEnabled ? jobInputDisabled : inputDisabled) || !isClassic}
					/>
				</label>
				<label className="text-sm text-gray-700">
					Previous line color
					<input
						type="color"
						className="mt-1 h-10 w-full rounded-md border border-gray-300"
						value={activeState.karaoke_previous_line_color}
						onChange={(e) => activeUpdate("karaoke_previous_line_color", e.target.value)}
						disabled={(overrideEnabled ? jobInputDisabled : inputDisabled) || !isClassic}
					/>
				</label>
				<label className="text-sm text-gray-700">
					Next line color
					<input
						type="color"
						className="mt-1 h-10 w-full rounded-md border border-gray-300"
						value={activeState.karaoke_next_line_color}
						onChange={(e) => activeUpdate("karaoke_next_line_color", e.target.value)}
						disabled={(overrideEnabled ? jobInputDisabled : inputDisabled) || !isClassic}
					/>
				</label>
				<label className="text-sm text-gray-700">
					Outline color
					<input
						type="color"
						className="mt-1 h-10 w-full rounded-md border border-gray-300"
						value={activeState.karaoke_outline_color}
						onChange={(e) => activeUpdate("karaoke_outline_color", e.target.value)}
						disabled={(overrideEnabled ? jobInputDisabled : inputDisabled) || !isClassic}
					/>
				</label>
				<label className="text-sm text-gray-700">
					Outline width
					<input
						type="number"
						min={0}
						step={0.5}
						className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
						value={activeState.karaoke_outline}
						onChange={(e) => activeUpdate("karaoke_outline", Number(e.target.value))}
						disabled={(overrideEnabled ? jobInputDisabled : inputDisabled) || !isClassic}
					/>
				</label>
				<label className="text-sm text-gray-700">
					Shadow
					<input
						type="number"
						min={0}
						step={0.5}
						className="mt-1 w-full rounded-md border border-gray-300 px-2 py-1.5 text-sm"
						value={activeState.karaoke_shadow}
						onChange={(e) => activeUpdate("karaoke_shadow", Number(e.target.value))}
						disabled={(overrideEnabled ? jobInputDisabled : inputDisabled) || !isClassic}
					/>
				</label>
			</div>

			{isClassic && (
				<div className="mt-4">
					<p className="mb-2 text-xs font-medium text-gray-600">Style preview</p>
					<ClassicStylePreview
						highlightColor={previewStyle.karaoke_highlight_color}
						previousColor={previewStyle.karaoke_previous_line_color}
						nextColor={previewStyle.karaoke_next_line_color}
						fontName={previewStyle.karaoke_font_name}
					/>
				</div>
			)}

			<div className="mt-4 flex flex-wrap gap-2">
				<SecondaryButton
					disabled={disabled || savingJobStyle}
					onClick={() => onSaveJobStyle?.(overridePayloadFromState(overrideEnabled, styleState))}
				>
					{savingJobStyle ? "Saving..." : "Save Job Style"}
				</SecondaryButton>
				{overrideEnabled && (
					<SecondaryButton disabled={disabled || savingJobStyle} onClick={() => onResetJobStyle?.()}>
						Reset to Site Default
					</SecondaryButton>
				)}
				{canEditSiteStyle && !overrideEnabled && (
					<SecondaryButton
						disabled={disabled || savingSiteStyle}
						onClick={() => onSaveSiteStyle?.(siteStyleState)}
					>
						{savingSiteStyle ? "Saving..." : "Save Site Style"}
					</SecondaryButton>
				)}
			</div>

			{!canEditSiteStyle && !overrideEnabled && (
				<p className="mt-3 text-xs text-gray-500">
					Site-wide subtitle style can only be changed by a System Manager. Enable per-job
					override above to customize this job.
				</p>
			)}
		</Card>
	);
}

export { styleFromEffective, overridePayloadFromState, STYLE_FIELDS };

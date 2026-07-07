import { useFrappeGetCall } from "frappe-react-sdk";
import AdminChecklist from "./AdminChecklist";
import { parseFrappeError, unwrapFrappeMessage } from "./utils";

export default function AdminSection() {
	const { data: checklistResponse, error: checklistError, isLoading } = useFrappeGetCall(
		"audio_stem.api.admin.get_configuration_checklist"
	);
	const checklistItems = unwrapFrappeMessage(checklistResponse) || [];

	return (
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
	);
}

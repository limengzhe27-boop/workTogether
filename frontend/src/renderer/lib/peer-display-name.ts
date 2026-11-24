import type { WorkspaceSession } from "../types/workspace";

/** One display policy for room messages, composer, permissions and task owners. */
export function peerDisplayName(
	session: Pick<WorkspaceSession, "id" | "title" | "provider">,
	override?: string,
): string {
	const title = (override || session.title || "").trim();
	if (title && title !== session.id) return title;
	const provider =
		(
			{
				codex: "Codex",
				"deepseek-harness": "DeepSeek",
				agy: "Antigravity",
			} as Record<string, string>
		)[session.provider] ||
		session.provider ||
		"Agent";
	const suffix = session.id.match(/(?:-|_)(\d+)$/)?.[1];
	return suffix ? `${provider} ${suffix}` : provider;
}

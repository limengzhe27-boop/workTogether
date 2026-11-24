import { expect, it } from "vitest";
import { peerDisplayName } from "./peer-display-name";
it("uses custom names first and a consistent provider name for generated session titles", () => {
	const s = {
		id: "worktogether-3",
		title: "worktogether-3",
		provider: "deepseek-harness" as const,
	};
	expect(peerDisplayName(s)).toBe("DeepSeek 3");
	expect(peerDisplayName({ ...s, title: "小安" })).toBe("小安");
	expect(peerDisplayName(s, "审查伙伴")).toBe("审查伙伴");
	expect(
		peerDisplayName({ id: "worktogether_2", title: "", provider: "codex" }),
	).toBe("Codex 2");
});

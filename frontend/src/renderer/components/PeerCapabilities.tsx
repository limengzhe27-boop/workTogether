import type { components } from "../../api/schema";
type Snapshot = components["schemas"]["ConversationSnapshotResponse"];
export function imageSupport(snapshot?: Snapshot) {
	if (
		!snapshot?.capabilities ||
		!["ready", "busy"].includes(snapshot.controller)
	)
		return "未知（尚未取得当前客户端声明）";
	return snapshot.capabilities.includes("images")
		? "客户端声明支持直接图片输入，当前模型未独立验证"
		: "未声明直接图片输入；工具读图能力待验证";
}
export function PeerCapabilities({
	snapshot,
	provider,
}: {
	snapshot?: Snapshot;
	provider?: string;
}) {
	const harness = snapshot?.harness || provider;
	const servers = snapshot?.mcpServers?.map((server) => server.name) ?? [];
	return (
		<details className="border-t border-border px-2 py-2 text-xs text-muted-foreground">
			<summary className="cursor-pointer">能力与权限</summary>
			<dl className="mt-2 space-y-2">
				<div>
					<dt className="inline font-medium text-foreground">当前会话 </dt>
					<dd className="inline">
						{harness || "客户端未知"}
						{snapshot?.settings?.model ? ` · ${snapshot.settings.model}` : ""}
					</dd>
				</div>
				<div>
					<dt className="inline font-medium text-foreground">读图 </dt>
					<dd className="inline">{imageSupport(snapshot)}</dd>
				</div>
				<div>
					<dt className="inline font-medium text-foreground">生图 </dt>
					<dd className="inline">
						{harness === "codex"
							? "可请求使用 Codex 内置生图。是否成功取决于当前客户端和账号，生成的图片随回复显示。"
							: "当前会话未报告生图能力。"}
					</dd>
				</div>
				<div>
					<dt className="inline font-medium text-foreground">文件与命令 </dt>
					<dd className="inline">
						按当前客户端工具与审批执行
						{snapshot?.settings?.approvalMode
							? `（${snapshot.settings.approvalMode}）`
							: ""}
						。
					</dd>
				</div>
			</dl>
			{servers.length > 0 && (
				<details className="mt-2">
					<summary className="cursor-pointer">
						已连接 MCP 服务（{servers.length}）
					</summary>
					<p className="mt-1 break-words">{servers.join("、")}</p>
				</details>
			)}
		</details>
	);
}

import { peerDisplayName } from "../lib/peer-display-name";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck, Hand, ShieldAlert, Check } from "lucide-react";
import { apiClient } from "../lib/api-client";
import type { WorkspaceSession } from "../types/workspace";
import type { components } from "../../api/schema";
import { Popover, PopoverTrigger, PopoverContent } from "./ui/popover";
import { Button } from "./ui/button";
type Mode = NonNullable<
	components["schemas"]["ConversationTurnSettingsPayload"]["approvalMode"]
>;
export function PeerAccessControl({
	sessions,
	disabled,
	onBusyChange,
}: {
	sessions: WorkspaceSession[];
	disabled: boolean;
	onBusyChange: (v: boolean) => void;
}) {
	const cache = useQueryClient();
	const [open, setOpen] = useState(false);
	const [selected, setSelected] = useState("");
	const [draft, setDraft] = useState<Mode | null>(null);
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState("");
	const session = sessions.find((s) => s.id === selected) ?? sessions[0];
	const key = ["peer-access", session?.id];
	const query = useQuery({
		queryKey: key,
		enabled: open && !!session,
		retry: false,
		staleTime: 0,
		queryFn: async () => {
			const [m, c] = await Promise.all([
				apiClient.GET("/api/v1/agents/chat/permission-modes", {
					params: { query: { agents: session.provider } },
				}),
				apiClient.GET("/api/v1/sessions/{sessionId}/conversation", {
					params: { path: { sessionId: session.id }, query: { limit: 1 } },
				}),
			]);
			if (m.error || c.error) throw new Error("无法读取此成员的原生权限");
			return {
				modes:
					m.data?.agents.find((a) => a.agent === session.provider)?.modes ?? [],
				settings: c.data?.settings ?? {},
			};
		},
	});
	const codex = session?.provider === "codex";
	const current = query.data?.settings.approvalMode ?? "default";
	const value = draft ?? current;
	const choices: {
		mode: Mode;
		label: string;
		description: string;
		icon: typeof Hand;
	}[] = codex
		? [
				{
					mode: "accept-edits",
					label: "请求批准",
					description: "访问工作区外文件或执行需要批准的操作时询问",
					icon: Hand,
				},
				{
					mode: "auto",
					label: "帮我批准",
					description: "由原生客户端评估风险，需要你决定时再询问",
					icon: ShieldCheck,
				},
				{
					mode: "bypass-permissions",
					label: "完全访问权限",
					description: "不再询问，允许访问文件及执行命令；仍受系统权限限制",
					icon: ShieldAlert,
				},
			]
		: [
				{
					mode: "default",
					label: "遵循客户端设置",
					description: "使用这位成员原生客户端配置的审批方式",
					icon: Hand,
				},
				{
					mode: "accept-edits",
					label: "允许文件编辑",
					description: "文件编辑可自动执行，其他操作遵循客户端审批",
					icon: Hand,
				},
				{
					mode: "auto",
					label: "自动审批",
					description: "使用客户端支持的自动审批模式",
					icon: ShieldCheck,
				},
				{
					mode: "bypass-permissions",
					label: "跳过审批",
					description: "客户端不再请求批准；仍受系统权限限制",
					icon: ShieldAlert,
				},
			];
	async function apply() {
		if (!session || !draft || saving || disabled || !query.data) return;
		setSaving(true);
		onBusyChange(true);
		setError("");
		try {
			const r = await apiClient.PATCH(
				"/api/v1/sessions/{sessionId}/conversation/settings",
				{
					params: { path: { sessionId: session.id } },
					body: { ...query.data.settings, approvalMode: draft },
				},
			);
			if (r.error) throw r.error;
			await cache.invalidateQueries({ queryKey: key });
			await cache.invalidateQueries({
				queryKey: ["peer-agent-settings", session.id],
			});
			setDraft(null);
		} catch (e) {
			setError(
				e && typeof e === "object" && "message" in e
					? String(e.message)
					: "权限未保存，请检查原生会话",
			);
		} finally {
			setSaving(false);
			onBusyChange(false);
		}
	}
	return (
		<Popover
			open={open}
			onOpenChange={(v) => {
				if (!saving) {
					setOpen(v);
					setDraft(null);
					setError("");
				}
			}}
		>
			<PopoverTrigger asChild>
				<button
					type="button"
					disabled={disabled || !sessions.length}
					className="inline-flex h-7 shrink-0 items-center gap-1 rounded-lg px-2 text-[length:var(--font-size-base)]! hover:bg-background focus-visible:ring-2 focus-visible:ring-ring"
					title="访问权限"
					aria-label="访问权限"
				>
					<ShieldCheck className="size-3.5" />
					<span className="hidden sm:inline">访问权限</span>
				</button>
			</PopoverTrigger>
			<PopoverContent
				side="top"
				align="start"
				className="w-96 max-w-[calc(100vw-2rem)] p-3"
			>
				<p className="mb-3 text-sm font-medium">如何批准这位成员的操作？</p>
				<select
					aria-label="权限所属成员"
					className="mb-3 w-full rounded-md border border-border bg-background p-2 text-sm"
					disabled={saving}
					value={session?.id ?? ""}
					onChange={(e) => {
						setSelected(e.target.value);
						setDraft(null);
						setError("");
					}}
				>
					{sessions.map((s) => (
						<option key={s.id} value={s.id}>
							{peerDisplayName(s)}
						</option>
					))}
				</select>
				{query.isPending && <p className="text-sm">读取原生权限…</p>}
				<div role="radiogroup" aria-label="原生访问权限">
					{choices
						.filter((c) => query.data?.modes.includes(c.mode))
						.map((c) => (
							<button
								key={c.mode}
								type="button"
								role="radio"
								aria-checked={
									value === c.mode ||
									(codex &&
										c.mode === "bypass-permissions" &&
										value === "default")
								}
								disabled={saving || disabled}
								className="flex min-h-14 w-full items-center gap-3 rounded-lg p-2 text-left hover:bg-secondary focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
								onClick={() => setDraft(c.mode)}
							>
								<c.icon className="size-4 shrink-0" />
								<span className="flex-1">
									<span className="block text-sm font-medium">{c.label}</span>
									<span className="block text-xs text-muted-foreground">
										{c.description}
									</span>
								</span>
								{(value === c.mode ||
									(codex &&
										c.mode === "bypass-permissions" &&
										value === "default")) && <Check className="size-4" />}
							</button>
						))}
				</div>
				{(error || query.isError) && (
					<p role="alert" className="my-2 text-sm text-destructive">
						{error || "无法读取权限，未作更改。"}
					</p>
				)}
				{query.data && !query.data.modes.length && (
					<p className="text-sm text-muted-foreground">
						此客户端未提供可切换的权限模式。
					</p>
				)}
				{draft && draft !== current && (
					<div className="mt-3 border-t border-border pt-3">
						<p className="mb-2 text-xs text-muted-foreground">
							只修改这位成员的后续操作，不影响其他成员。
						</p>
						<Button disabled={saving || disabled} onClick={() => void apply()}>
							{saving ? "正在保存…" : "应用权限"}
						</Button>
					</div>
				)}
			</PopoverContent>
		</Popover>
	);
}

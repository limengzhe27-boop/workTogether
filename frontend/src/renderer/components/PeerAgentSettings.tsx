import { PeerCapabilities } from "./PeerCapabilities";
import { Switch } from "./ui/switch";
import { peerDisplayName } from "../lib/peer-display-name";
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../lib/api-client";
import type { WorkspaceSession } from "../types/workspace";
import type { components } from "../../api/schema";
import { ExternalLink, UserMinus } from "lucide-react";
import {
	OptionMenu,
	OptionMenuContent,
	OptionMenuTrigger,
} from "./ui/option-menu";
import { AgentAvatar } from "./AgentAvatar";
import { TurnSettingsBar } from "./chat/TurnSettingsBar";
import type { ChatConfigOptionValue } from "../types/conversation";

type Settings = components["schemas"]["ConversationTurnSettingsPayload"];
export type PeerDeliveryProblem = { status: string; detail?: string };

function errorCode(error: unknown) {
	return error && typeof error === "object" && "code" in error
		? String(error.code)
		: "";
}

function message(e: unknown) {
	const code = errorCode(e);
	const known: Record<string, string> = {
		CHAT_CONTROLLER_NOT_READY: "客户端尚未连接。发送新消息时会尝试恢复会话。",
		CHAT_AUTH_REQUIRED: "客户端需要登录，请打开原生会话完成登录。",
		CHAT_DRIVER_UNAVAILABLE: "找不到客户端，请检查安装后刷新状态。",
		CHAT_DRIVER_INCOMPATIBLE: "客户端版本不兼容，请检查安装版本。",
		AGENT_RESUME_IN_PROGRESS: "客户端正在连接，请稍后刷新状态。",
	};
	if (known[code]) return known[code];
	return e && typeof e === "object" && "message" in e
		? String(e.message)
		: "设置未保存，请稍后再试";
}

/** Uses each native session's actual catalog, never a shared invented model list. */
export function PeerAgentSettings({
	session,
	issue,
	deliveryProblem,
	onRefreshStatus,
	disabled,
	onBusyChange,
	onRenamed,
	onOpenSession,
	usage,
	snapshot,
	memberSettings,
	onMemberSettingsChange,
	onRemove,
	removeDisabledReason,
}: {
	session: WorkspaceSession;
	onRemove?: () => Promise<boolean>;
	removeDisabledReason?: string;
	snapshot?: components["schemas"]["ConversationSnapshotResponse"];
	memberSettings?: { disabled: boolean; peerLimit?: number | null };
	onMemberSettingsChange?: (setting: {
		disabled: boolean;
		peerLimit?: number | null;
	}) => Promise<void>;
	usage?: { contextUsed: number; contextWindow: number };
	issue: string;
	deliveryProblem?: PeerDeliveryProblem;
	onRefreshStatus?: () => Promise<void>;
	disabled: boolean;
	onBusyChange: (busy: boolean) => void;
	onRenamed: (name: string) => void;
	onOpenSession?: () => void;
}) {
	const policy = memberSettings ?? { disabled: false };
	const cache = useQueryClient();
	const compactName = peerDisplayName(session);
	const [open, setOpen] = useState(false);
	const [name, setName] = useState(compactName);
	const [saving, setSaving] = useState(false);
	const [error, setError] = useState("");
	const savedName = useRef(compactName);
	const nameWrite = useRef<Promise<boolean> | null>(null);
	const savingRef = useRef(false);
	const known =
		!!usage &&
		Number.isFinite(usage.contextUsed) &&
		Number.isFinite(usage.contextWindow) &&
		usage.contextUsed >= 0 &&
		usage.contextWindow > 0;
	const percent = known
		? Math.min(
				100,
				Math.max(0, (usage!.contextUsed / usage!.contextWindow) * 100),
			)
		: undefined;
	const contextLabel =
		percent === undefined
			? "上下文用量未上报"
			: `上下文已用 ${Math.round(percent)}%（${usage!.contextUsed.toLocaleString()} / ${usage!.contextWindow.toLocaleString()} tokens）`;
	const key = ["peer-agent-settings", session.id];
	const catalog = useQuery({
		queryKey: key,
		enabled: open && !issue && snapshot?.controller !== "stopped",
		retry: false,
		staleTime: 0,
		refetchOnWindowFocus: false,
		queryFn: async () => {
			const [models, config] = await Promise.all([
				apiClient.GET("/api/v1/sessions/{sessionId}/conversation/models", {
					params: { path: { sessionId: session.id } },
				}),
				apiClient.GET(
					"/api/v1/sessions/{sessionId}/conversation/config-options",
					{ params: { path: { sessionId: session.id } } },
				),
			]);
			if (models.error || config.error) throw models.error || config.error;
			return {
				models: models.data?.models ?? [],
				settings: models.data?.selected ?? {},
				options: (config.data?.options ?? []).map((option) => ({
					...option,
					choices: option.choices ?? [],
					currentBoolean: option.currentBoolean ?? undefined,
				})),
			};
		},
	});
	async function save(action: () => Promise<void>) {
		if (savingRef.current || disabled) return false;
		savingRef.current = true;
		setSaving(true);
		onBusyChange(true);
		setError("");
		try {
			await action();
			await cache.invalidateQueries({ queryKey: key });
			await cache.invalidateQueries({ queryKey: ["peer-room-conversation"] });
			return true;
		} catch (e) {
			setError(message(e));
			return false;
		} finally {
			savingRef.current = false;
			setSaving(false);
			onBusyChange(false);
		}
	}
	async function nativeOption(id: string, value: ChatConfigOptionValue) {
		const r = await apiClient.PATCH(
			"/api/v1/sessions/{sessionId}/conversation/config-options/{configId}",
			{
				params: { path: { sessionId: session.id, configId: id } },
				body: value,
			},
		);
		if (r.error) throw r.error;
	}
	async function settings(next: Settings) {
		const r = await apiClient.PATCH(
			"/api/v1/sessions/{sessionId}/conversation/settings",
			{
				params: { path: { sessionId: session.id } },
				body: { ...catalog.data?.settings, ...next },
			},
		);
		if (r.error) throw r.error;
	}
	async function rename(): Promise<boolean> {
		if (nameWrite.current) return nameWrite.current;
		const next = name.trim();
		if (next === savedName.current) return true;
		if (!next) {
			setError("名称不能为空");
			return false;
		}
		const pending = save(async () => {
			const r = await apiClient.PATCH("/api/v1/sessions/{sessionId}", {
				params: { path: { sessionId: session.id } },
				body: { displayName: next },
			});
			if (r.error) throw r.error;
			savedName.current = next;
			setName(next);
			onRenamed(next);
			await cache.invalidateQueries({ queryKey: ["workspaces"] });
		});
		nameWrite.current = pending;
		try {
			return await pending;
		} finally {
			nameWrite.current = null;
		}
	}
	const data = catalog.data;
	const blocked = saving || disabled || !!issue;
	const disconnected =
		snapshot?.controller === "stopped" || snapshot?.controller === "hibernated";
	const connectionStatus = disconnected
		? "会话未连接，发送新消息时自动连接"
		: snapshot?.controller === "busy"
			? "正在处理消息"
			: "可发送文字";
	const catalogCode = errorCode(catalog.error);
	const diagnostic = [
		"CHAT_AUTH_REQUIRED",
		"CHAT_CONTROLLER_NOT_READY",
		"CHAT_DRIVER_UNAVAILABLE",
		"CHAT_DRIVER_INCOMPATIBLE",
		"AGENT_RESUME_IN_PROGRESS",
	].includes(catalogCode) ? message(catalog.error) : "";
	const refreshable = !session.isTerminated && !policy.disabled;
	const [refreshing, setRefreshing] = useState(false);
	async function refreshStatus() {
		setRefreshing(true);
		try {
			await Promise.all([
				onRefreshStatus?.(),
				cache.invalidateQueries({ queryKey: key }),
			]);
		} finally {
			setRefreshing(false);
		}
	}
	return (
		<OptionMenu
			modal={false}
			open={open}
			onOpenChange={(v) => {
				if (v) {
					setOpen(true);
					savedName.current = compactName;
					setName(savedName.current);
					setError("");
				} else if (name.trim() === savedName.current) {
					setOpen(false);
				} else {
					void rename().then((ok) => {
						if (ok) setOpen(false);
					});
				}
			}}
		>
			<OptionMenuTrigger
				showCaret={false}
				type="button"
				aria-label={`设置成员 ${compactName}`}
				title={`${compactName} · ${issue || diagnostic || deliveryProblem?.status || connectionStatus} · ${contextLabel}`}
				className="inline-flex h-7 max-w-32 shrink-0 items-center gap-1.5 rounded-lg px-1.5 text-[length:var(--font-size-base)]! text-muted-foreground hover:bg-background hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring"
				disabled={disabled}
			>
				<span
					className="relative inline-flex size-6 shrink-0 items-center justify-center"
					role="img"
					aria-label={contextLabel}
				>
					<svg
						viewBox="0 0 36 36"
						className="absolute inset-0 size-6 -rotate-90"
						aria-hidden="true"
					>
						<circle
							cx="18"
							cy="18"
							r="16"
							fill="none"
							stroke="currentColor"
							strokeWidth="2"
							className="text-border"
							strokeDasharray={known ? undefined : "2 3"}
						/>
						{percent !== undefined && (
							<circle
								cx="18"
								cy="18"
								r="16"
								fill="none"
								stroke="currentColor"
								strokeWidth="2"
								pathLength="100"
								strokeDasharray={`${percent} 100`}
								strokeLinecap="round"
								className={
									percent >= 90
										? "text-status-exited"
										: percent >= 70
											? "text-status-needs-you"
											: "text-muted-foreground"
								}
							/>
						)}
					</svg>
					<AgentAvatar
						provider={session.provider}
						className="size-3.5 shrink-0"
						decorative
					/>
				</span>
				<span className="max-w-24 truncate">{compactName}</span>
				{(issue || deliveryProblem || diagnostic) && (
					<span
						className="size-1.5 rounded-full bg-destructive"
						aria-label={issue ? "暂不可用" : "有状态提示"}
					/>
				)}
			</OptionMenuTrigger>
			<OptionMenuContent
				side="top"
				align="start"
				className="chat-settings-menu w-64 max-w-[calc(100vw-2rem)] text-[12px]!"
				onEscapeKeyDown={(e) => {
					if (name !== savedName.current) {
						e.preventDefault();
						setName(savedName.current);
						setError("");
					}
				}}
			>
				<div className="flex items-center gap-1 border-b border-border/60 px-1 pb-1 mb-1">
					<label className="block min-w-0 flex-1">
						<span className="sr-only">成员名称</span>
						<input
							aria-label="成员名称"
							className="w-full rounded-md bg-transparent px-2 py-1.5 text-[12px]! outline-none hover:bg-secondary focus:bg-secondary focus-visible:ring-1 focus-visible:ring-ring"
							maxLength={100}
							value={name}
							disabled={saving || disabled}
							onChange={(e) => setName(e.target.value)}
							onBlur={() => void rename()}
							onKeyDown={(e) => {
								if (e.key !== "Escape" && e.key !== "Tab") e.stopPropagation();
								if (
									e.key === "Enter" &&
									!e.nativeEvent.isComposing &&
									e.keyCode !== 229
								) {
									e.preventDefault();
									void rename();
								}
							}}
						/>
					</label>
					{onMemberSettingsChange && (
						<Switch
							aria-label="启用成员"
							title={policy.disabled ? "启用成员" : "停用成员"}
							className="mx-2 after:hidden"
							checked={!policy.disabled}
							disabled={saving || disabled}
							onCheckedChange={(checked) =>
								void save(() =>
									onMemberSettingsChange({ ...policy, disabled: !checked }),
								)
							}
						/>
					)}
					{onOpenSession && (
						<button
							type="button"
							aria-label="打开原生会话"
							title="打开原生会话"
							onClick={onOpenSession}
							className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-foreground"
						>
							<ExternalLink className="size-3.5" />
						</button>
					)}
					{onRemove && (
						<button
							type="button"
							aria-label="移出群聊"
							title={removeDisabledReason || "移出群聊（保留原生会话）"}
							className="inline-flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-secondary hover:text-destructive disabled:opacity-40"
							disabled={saving || disabled || !!removeDisabledReason}
							onClick={async () => {
								if (savingRef.current) return;
								savingRef.current = true;
								setSaving(true);
								try {
									if (await onRemove()) setOpen(false);
								} finally {
									savingRef.current = false;
									setSaving(false);
								}
							}}
						>
							<UserMinus className="size-3.5" aria-hidden="true" />
						</button>
					)}
				</div>
				{onMemberSettingsChange && (
					<div className="space-y-1 border-b border-border/60 pb-1 mb-1">
						<label className="flex items-center justify-between gap-2 px-2 py-1.5">
							主动联系同伴
							<select
								aria-label="主动联系同伴"
								className="min-w-0 bg-transparent text-muted-foreground"
								disabled={saving || disabled}
								value={
									policy.peerLimit == null
										? "unlimited"
										: policy.peerLimit === 0
											? "off"
											: "limited"
								}
								onChange={(e) =>
									void save(() =>
										onMemberSettingsChange({
											...policy,
											peerLimit:
												e.target.value === "unlimited"
													? undefined
													: e.target.value === "off"
														? 0
														: 8,
										}),
									)
								}
							>
								<option value="unlimited">不限制</option>
								<option value="limited">每轮限制</option>
								<option value="off">关闭</option>
							</select>
						</label>
						{policy.peerLimit != null && policy.peerLimit > 0 && (
							<label className="flex items-center justify-between px-2 py-1.5">
								每轮最多
								<input
									aria-label="每轮主动联系次数"
									type="number"
									min={1}
									max={10000}
									className="w-16 rounded bg-secondary px-2 py-1"
									key={policy.peerLimit}
									defaultValue={policy.peerLimit}
									disabled={saving || disabled}
									onBlur={(e) => {
										const n = Number(e.target.value);
										if (
											Number.isInteger(n) &&
											n > 0 &&
											n <= 10000 &&
											n !== policy.peerLimit
										)
											void save(() =>
												onMemberSettingsChange({ ...policy, peerLimit: n }),
											);
									}}
									onKeyDown={(e) => {
										if (e.key === "Enter") {
											e.preventDefault();
											e.currentTarget.blur();
										}
									}}
								/>
							</label>
						)}
					</div>
				)}
				<div className="space-y-1 px-2 py-1.5 text-xs text-muted-foreground">
					<p role="status">{issue || diagnostic || connectionStatus}</p>
					{deliveryProblem && (
						<p className="break-words">
							<span className="font-medium text-foreground">{deliveryProblem.status}</span>
							{deliveryProblem.detail && ` · ${deliveryProblem.detail}`}
						</p>
					)}
					{refreshable && (issue || disconnected || diagnostic || deliveryProblem || catalog.isError) && (
						<div className="flex flex-wrap gap-3">
							<button
								type="button"
								className="underline underline-offset-2"
								disabled={refreshing || saving || disabled}
								onClick={() => void refreshStatus()}
							>
								{refreshing ? "正在刷新…" : "刷新状态"}
							</button>
							{onOpenSession && (
								<button
									type="button"
									className="underline underline-offset-2"
									onClick={onOpenSession}
								>
									{catalogCode === "CHAT_AUTH_REQUIRED" ? "打开原生会话登录" : "检查原生会话"}
								</button>
							)}
						</div>
					)}
				</div>
				{!issue && !diagnostic && (
					<>
						{catalog.isFetching && (
							<p className="text-xs text-muted-foreground">
								正在读取模型…
							</p>
						)}
						{data && (
							<TurnSettingsBar
								embedded
								harness={session.provider}
								models={data.models}
								settings={data.settings}
								configOptions={data.options}
								showApprovalMode={false}
								disabled={blocked}
								configPending={saving}
								onChange={(next) => void save(() => settings(next))}
								onChangeConfigOption={(id, value) =>
									save(() => nativeOption(id, value))
								}
							/>
						)}
					</>
				)}
				<PeerCapabilities snapshot={snapshot} provider={session.provider} />
				{(error || (catalog.isError && !diagnostic && !issue)) && (
					<p role="alert" className="text-sm text-destructive">
						{error || "模型选项暂时无法加载，请刷新状态。"}
					</p>
				)}

				{saving && (
					<p role="status" className="text-xs">
						正在保存…
					</p>
				)}
			</OptionMenuContent>
		</OptionMenu>
	);
}

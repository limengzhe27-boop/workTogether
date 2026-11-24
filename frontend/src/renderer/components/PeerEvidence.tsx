import { PeerExecution } from "./PeerExecution";
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiClient } from "../lib/api-client";
export function PeerTaskEvidence({
	roomId,
	taskId,
	label,
	compact = false,
	linkedDelivery,
}: {
	roomId: string;
	taskId: string;
	compact?: boolean;
	linkedDelivery?: { id: string; owner: string };
	label: (id: string) => string;
}) {
	const [open, setOpen] = useState(false);
	const q = useQuery({
		queryKey: ["peer-task-evidence", roomId, taskId],
		enabled: open,
		refetchInterval: open ? 3000 : false,
		retry: false,
		queryFn: async () => {
			const r = await apiClient.GET(
				"/api/v1/peer-rooms/{roomId}/tasks/{taskId}/evidence",
				{ params: { path: { roomId, taskId } } },
			);
			if (r.error) throw Error("证据历史读取失败");
			return r.data ?? [];
		},
	});
	return (
		<details onToggle={(e) => setOpen(e.currentTarget.open)}>
			<summary className="cursor-pointer">
				{compact ? "查看改动与测试记录" : "提交与独立验收历史"}
			</summary>
			{open && (
				<p className="mt-2 text-xs text-muted-foreground">
					文件与测试结果为成员报告。可通过关联的执行记录核对工具输出。
				</p>
			)}
			{open && q.isPending && <p>正在加载记录…</p>}
			{q.isError && <p role="alert">证据历史读取失败</p>}
			{q.data
				?.filter(
					(a) => !compact || ["submit", "accept", "reject"].includes(a.action),
				)
				.map((a) => (
					<div key={a.revision} className="my-2 text-xs">
						<p>
							{a.actor ? label(a.actor) : "你"} ·{" "}
							{(
								{
									submit: "提交",
									accept: "验收通过",
									reject: "验收未通过",
									propose: "提出",
									claim: "认领",
									release: "放回待认领",
								} as Record<string, string>
							)[a.action] ?? a.action}
						</p>
						{a.commit && <code className="break-all">{a.commit}</code>}
						{a.evidence && <pre className="whitespace-pre-wrap break-words">{a.evidence}</pre>}
						{a.files?.length ? (
							<p>声明的变更文件：{a.files.join("、")}</p>
						) : null}
						{a.tests?.map((test, i) => (
							<pre key={i} className="whitespace-pre-wrap break-words">
								成员报告的检查：{test.command}
								{"\n"}目录：{test.cwd} · 退出码：{test.exitCode}
								{"\n"}范围：{test.scope}
							</pre>
						))}
						{[
							...new Set([
								...(a.executions ?? []),
								...(a.deliveryId ? [a.deliveryId] : []),
							]),
						].map((id) => (
							<PeerExecution
								key={id}
								roomId={roomId}
								deliveryId={id}
								sessionId={a.actor}
							/>
						))}
					</div>
				))}
			{open &&
				q.data &&
				linkedDelivery &&
				!q.data.some(
					(action) =>
						action.deliveryId === linkedDelivery.id ||
						action.executions?.includes(linkedDelivery.id),
				) && (
					<PeerExecution
						roomId={roomId}
						deliveryId={linkedDelivery.id}
						sessionId={linkedDelivery.owner}
					/>
				)}
		</details>
	);
}
export function PeerIntegrationEvidence({
	roomId,
	epoch,
	active = true,
}: {
	roomId: string;
	epoch: number;
	active?: boolean;
}) {
	const cache = useQueryClient();
	const key = ["peer-integration-evidence", roomId];
	const [commit, setCommit] = useState("");
	const [executor, setExecutor] = useState("");
	const [command, setCommand] = useState("");
	const [cwd, setCwd] = useState("");
	const [exit, setExit] = useState("");
	const [scope, setScope] = useState("");
	const [notice, setNotice] = useState("");
	const [busy, setBusy] = useState(false);
	const q = useQuery({
		queryKey: key,
		enabled: active,
		retry: false,
		refetchInterval: active ? 5000 : false,
		queryFn: async () => {
			const r = await apiClient.GET(
				"/api/v1/peer-rooms/{roomId}/integration-evidence",
				{ params: { path: { roomId } } },
			);
			if (r.error) throw Error("集成记录读取失败");
			return r.data ?? [];
		},
	});
	async function save() {
		if (busy) return;
		setBusy(true);
		setNotice("");
		try {
			const r = await apiClient.POST(
				"/api/v1/peer-rooms/{roomId}/integration-evidence",
				{
					params: { path: { roomId } },
					body: {
						id: crypto.randomUUID(),
						epoch,
						commit,
						executor,
						command,
						cwd,
						exitCode: Number(exit),
						scope,
						createdAt: "",
					},
				},
			);
			if (r.error) throw Error("记录未确认保存，请刷新核查，不会自动重试。");
			setNotice("已保存手动测试声明；不代表平台独立验收通过。");
			setCommand("");
			setExit("");
			setScope("");
			await cache.invalidateQueries({ queryKey: key });
		} catch (e) {
			setNotice(String(e));
		} finally {
			setBusy(false);
		}
	}
	return (
		<details className="mt-3 text-sm">
			<summary>候选版本与集成测试记录</summary>
			<p className="my-2 text-xs text-muted-foreground">
				候选生成与测试是两个步骤。这里不执行命令，手动记录只代表填写人的声明，不自动标记最终通过。
			</p>
			{q.isError && <p role="alert">集成记录读取失败</p>}
			{q.data?.map((c) => (
				<div key={c.commit} className="my-3">
					<p>{c.branch}</p>
					<code className="break-all text-xs">{c.commit}</code>
					{!c.checks.length && <p>未记录集成测试</p>}
					{c.checks.map((t) => (
						<details key={t.id}>
							<summary>
								{t.executor} · 手动记录 · 退出码 {t.exitCode}
							</summary>
							<pre className="whitespace-pre-wrap break-words">
								{t.cwd}
								{"\n"}
								{t.command}
								{"\n"}
								{t.scope}
							</pre>
						</details>
					))}
				</div>
			))}
			{!!q.data?.length && (
				<div className="grid gap-2">
					<select
						aria-label="测试候选版本"
						value={commit}
						onChange={(e) => setCommit(e.target.value)}
					>
						<option value="">选择固定候选提交</option>
						{q.data.map((c) => (
							<option key={c.commit} value={c.commit}>
								{c.branch} · {c.commit.slice(0, 12)}
							</option>
						))}
					</select>
					<input
						aria-label="执行人"
						placeholder="执行人"
						value={executor}
						onChange={(e) => setExecutor(e.target.value)}
					/>
					<input
						aria-label="测试命令"
						placeholder="实际执行的测试命令"
						value={command}
						onChange={(e) => setCommand(e.target.value)}
					/>
					<input
						aria-label="执行目录"
						placeholder="实际执行目录"
						value={cwd}
						onChange={(e) => setCwd(e.target.value)}
					/>
					<input
						aria-label="退出码"
						type="number"
						placeholder="实际退出码"
						value={exit}
						onChange={(e) => setExit(e.target.value)}
					/>
					<textarea
						aria-label="验收范围"
						placeholder="独立重跑了哪些检查、哪些未验证；相关产物及日志路径"
						value={scope}
						onChange={(e) => setScope(e.target.value)}
					/>
					<button
						type="button"
						disabled={
							busy ||
							!commit ||
							!executor.trim() ||
							!command.trim() ||
							!cwd.trim() ||
							!scope.trim() ||
							exit === "" ||
							!Number.isInteger(Number(exit))
						}
						onClick={save}
					>
						保存手动测试记录
					</button>
				</div>
			)}
			{notice && <p role="status">{notice}</p>}
		</details>
	);
}

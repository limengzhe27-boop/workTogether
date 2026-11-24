import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";
import { TooltipProvider } from "./ui/tooltip";
import { aoBridge } from "../lib/bridge";
import { setApiBaseUrl } from "../lib/api-client";
import {
	render as rtlRender,
	screen,
	cleanup,
	fireEvent,
	waitFor,
} from "@testing-library/react";
import { afterEach, it, expect, vi } from "vitest";
import {
	PeerTimeline,
	peerMessageGroups,
	orderPeerReplies,
} from "./PeerTimeline";
import type { components } from "../../api/schema";
vi.mock("./chat/ChatMarkdown", () => ({
	ChatMarkdown: ({ text }: { text: string }) => <p>{text}</p>,
}));
const render = (ui: ReactElement) =>
	rtlRender(ui, { wrapper: TooltipProvider });
type Delivery = components["schemas"]["PeerDelivery"];
const a = {
	id: "a",
	broadcastId: "broadcast1",
	epoch: 1,
	sender: "",
	target: "one",
	message: "一起工作",
	state: "accepted",
	replies: [{ id: "r1", text: "我的分析" }],
} as Delivery;
afterEach(cleanup);
it("renders a broadcast once with separately identified replies", () => {
	render(
		<PeerTimeline
			deliveries={[
				a,
				{
					...a,
					id: "b",
					target: "two",
					replies: [
						{
							id: "r2",
							createdAt: "2026-11-13T09:00:00Z",
							text: "我的验证",
							streaming: false,
							turnState: "completed",
						},
					],
				},
			]}
			label={(id) => id}
			provider={() => "codex"}
			status={() => "已回复"}
		/>,
	);
	expect(screen.getAllByText("一起工作")).toHaveLength(1);
	expect(
		screen.getByRole("article", { name: "来自 one 的回复" }),
	).toHaveTextContent("我的分析");
	expect(
		screen.getByRole("article", { name: "来自 two 的回复" }),
	).toHaveTextContent("我的验证");
});
it("renders the persisted image only under its generating member and rejects unrelated URLs", () => {
	const base = "http://127.0.0.1:3042";
	const path = `/api/v1/sessions/one/artifact-files/generated-image-${"a".repeat(64)}.png`;
	setApiBaseUrl(base);
	try {
		const deliveries = [{
			...a,
			generatedImages: [{ id: "image1", name: "生成图", url: path, status: "completed" }],
		}, {
			...a,
			id: "b",
			target: "two",
			generatedImages: [{ id: "image2", name: "外部图", url: "https://example.org/image.png", status: "completed" }],
		}];
		const props = { deliveries, label: (id: string) => id, provider: () => "codex", status: () => "已回复" };
		const view = render(<PeerTimeline {...props} />);
		expect(screen.getByRole("article", { name: "来自 one 的回复" })).toContainElement(screen.getByAltText("生成图"));
		expect(screen.getByAltText("生成图")).toHaveAttribute("src", base + path);
		expect(screen.queryByAltText("外部图")).toBeNull();
		view.unmount();
		render(<PeerTimeline {...props} />);
		expect(screen.getAllByAltText("生成图")).toHaveLength(1);
	} finally {
		cleanup();
		setApiBaseUrl(null);
	}
});
it("replaces the generation placeholder and closes full-size previews without losing the thumbnail", async () => {
	const base = "http://127.0.0.1:3042";
	const path = `/api/v1/sessions/one/artifact-files/generated-image-${"b".repeat(64)}.png`;
	setApiBaseUrl(base);
	try {
		const props = { label: (id: string) => id, provider: () => "codex", status: () => "正在工作" };
		const timeline = (status: string, url?: string) => <PeerTimeline {...props} deliveries={[{
			...a, replies: [], generatedImages: [{ id: "same-image", status, url }],
		}]} />;
		const view = render(timeline("running"));
		expect(screen.getByRole("status")).toHaveTextContent("正在生成图片…");
		expect(screen.queryByRole("img")).toBeNull();
		view.rerender(timeline("completed", path));
		expect(screen.getByRole("status")).toHaveTextContent("正在加载图片…");
		const thumbnail = screen.getByRole("img", { name: "生成的图片" });
		const thumbnailButton = thumbnail.closest("button");
		expect(thumbnailButton).toBeDisabled();
		fireEvent.load(thumbnail);
		expect(screen.queryByRole("status")).toBeNull();
		expect(thumbnailButton).toBeEnabled();
		fireEvent.click(thumbnailButton!);
		const viewer = screen.getByRole("dialog");
		expect(viewer).toBeVisible();
		fireEvent.click(viewer.querySelector("img")!);
		expect(viewer).toBeVisible();
		fireEvent.click(viewer);
		expect(screen.queryByRole("dialog")).toBeNull();
		await waitFor(() => expect(thumbnailButton).toHaveFocus());
		fireEvent.click(thumbnailButton!);
		fireEvent.click(screen.getByRole("button", { name: "关闭大图" }));
		expect(screen.queryByRole("dialog")).toBeNull();
		view.rerender(timeline("failed"));
		expect(screen.getByRole("status")).toHaveTextContent("图片生成失败");
		expect(screen.queryByRole("img")).toBeNull();
		view.rerender(timeline("cancelled"));
		expect(screen.getByRole("status")).toHaveTextContent("图片生成已中断");
	} finally {
		cleanup();
		setApiBaseUrl(null);
	}
});
it("shows image load errors and retries only the preview URL", () => {
	setApiBaseUrl("http://127.0.0.1:3042");
	try {
		const path = `/api/v1/sessions/one/artifact-files/generated-image-${"a".repeat(64)}.png`;
		render(<PeerTimeline deliveries={[{
			...a, generatedImages: [{ id: "image1", status: "completed", url: path }],
		}]} label={(id) => id} provider={() => "codex"} status={() => "已回复"} />);
		fireEvent.error(screen.getByRole("img", { name: "生成的图片" }));
		expect(screen.getByRole("status")).toHaveTextContent("图片预览加载失败");
		fireEvent.click(screen.getByRole("button", { name: "重试预览" }));
		const image = screen.getByRole("img", { name: "生成的图片" });
		expect(image).toHaveAttribute("src", `http://127.0.0.1:3042${path}?preview=1`);
		fireEvent.load(image);
		expect(screen.queryByRole("status")).toBeNull();
	} finally {
		cleanup();
		setApiBaseUrl(null);
	}
});
it("never merges identical text across broadcasts or guesses legacy grouping", () => {
	expect(
		peerMessageGroups([
			a,
			{ ...a, id: "b", broadcastId: "broadcast2" },
			{ ...a, id: "c", broadcastId: undefined },
			{ ...a, id: "d", broadcastId: undefined },
		]),
	).toHaveLength(4);
});
it("keeps attachment metadata visible after reloading and never merges changed files", () => {
	const withFile = {
		...a,
		message: "",
		attachments: [
			{ name: "图.png", mimeType: "image/png", size: 2048, sha256: "abc" },
		],
	};
	render(
		<PeerTimeline
			deliveries={[withFile, { ...withFile, id: "b", target: "two" }]}
			label={(id) => id}
			provider={() => "codex"}
			status={() => "已回复"}
		/>,
	);
	expect(screen.getAllByText("图.png · 2 KB")).toHaveLength(1);
	expect(
		peerMessageGroups([
			withFile,
			{
				...withFile,
				id: "c",
				attachments: [{ ...withFile.attachments[0], sha256: "other" }],
			},
		]),
	).toHaveLength(2);
});
it("collapses earlier updates once per member and keeps the latest streaming answer visible", () => {
	const replies = [
		{
			id: "p1",
			createdAt: "2026-11-13T09:00:00Z",
			text: "先看仓库",
			streaming: false,
			turnState: "running",
		},
		{
			id: "p2",
			createdAt: "2026-11-13T09:00:01Z",
			text: "再看 README",
			streaming: false,
			turnState: "running",
		},
		{
			id: "last",
			createdAt: "2026-11-13T09:00:02Z",
			text: "当前答复",
			streaming: true,
			turnState: "running",
		},
	];
	const view = render(
		<PeerTimeline
			deliveries={[{ ...a, replies }]}
			label={() => "小安"}
			provider={() => "codex"}
			status={() => "正在工作"}
		/>,
	);
	const details = screen.getByText("查看过程 · 2 条更新").closest("details");
	expect(details).not.toHaveAttribute("open");
	expect(details).toContainElement(screen.getByText("先看仓库"));
	expect(details).not.toContainElement(screen.getByText("当前答复"));
	expect(screen.getAllByText("小安")).toHaveLength(1);
	view.rerender(
		<PeerTimeline
			deliveries={[
				{
					...a,
					replies: [
						...replies.slice(0, -1),
						{
							...replies[2],
							text: "最终答复",
							streaming: false,
							turnState: "completed",
						},
					],
				},
			]}
			label={() => "小安"}
			provider={() => "codex"}
			status={() => "本轮已回复"}
		/>,
	);
	expect(screen.getByText("最终答复")).toBeInTheDocument();
	expect(
		screen.getByText("查看过程 · 2 条更新").closest("details"),
	).not.toHaveAttribute("open");
});
it("does not hide a single reply behind a disclosure", () => {
	render(
		<PeerTimeline
			deliveries={[a]}
			label={() => "小安"}
			provider={() => "codex"}
			status={() => "本轮已回复"}
		/>,
	);
	expect(screen.queryByText(/查看过程/)).not.toBeInTheDocument();
	expect(screen.getByText("我的分析")).toBeVisible();
});

const reply = (id: string, createdAt: string, text = id) => ({
	id,
	createdAt,
	text,
	streaming: true,
	turnState: "running",
});
it("orders responses by first reply rather than creation order, including after reload", () => {
	const deliveries = [
		{ ...a, target: "GPT1", replies: [reply("g1", "2026-11-13T09:00:03Z")] },
		{
			...a,
			id: "b",
			target: "DeepSeek",
			replies: [reply("d", "2026-11-13T09:00:01Z")],
		},
		{
			...a,
			id: "c",
			target: "GPT2",
			replies: [reply("g2", "2026-11-13T09:00:02Z")],
		},
	];
	const props = {
		label: (id: string) => id,
		provider: () => "codex",
		status: () => "正在回复",
	};
	const view = render(<PeerTimeline {...props} deliveries={deliveries} />);
	const order = () =>
		screen.getAllByRole("article").map((el) => el.getAttribute("aria-label"));
	expect(order()).toEqual([
		"来自 DeepSeek 的回复",
		"来自 GPT2 的回复",
		"来自 GPT1 的回复",
	]);
	// Later commentary/final messages must not move the first responder down.
	deliveries[1].replies.push(reply("final", "2026-11-13T09:01:00Z"));
	view.rerender(<PeerTimeline {...props} deliveries={deliveries} />);
	expect(order()[0]).toBe("来自 DeepSeek 的回复");
	view.unmount();
	render(
		<PeerTimeline
			{...props}
			deliveries={JSON.parse(JSON.stringify(deliveries))}
		/>,
	);
	expect(order()[0]).toBe("来自 DeepSeek 的回复");
	expect(deliveries.map((d) => d.target)).toEqual(["GPT1", "DeepSeek", "GPT2"]);
});
it("keeps pending, empty and failed responses behind actual replies with stable ties", () => {
	const deliveries = [
		{ ...a, id: "pending", replies: [] },
		{
			...a,
			id: "empty",
			replies: [reply("empty", "2026-11-13T08:00:00Z", "  ")],
		},
		{ ...a, id: "failed", state: "uncertain", replies: [] },
		{ ...a, id: "fast", replies: [reply("fast", "2026-11-13T09:00:00Z")] },
		{ ...a, id: "tie", replies: [reply("tie", "2026-11-13T09:00:00Z")] },
		{ ...a, id: "legacy", replies: [reply("old", "invalid")] },
	];
	expect(orderPeerReplies(deliveries).map((d) => d.id)).toEqual([
		"fast",
		"tie",
		"legacy",
		"pending",
		"empty",
		"failed",
	]);
});

it("copies each reply as complete Markdown without names or hidden updates", async () => {
	const copy = vi
		.spyOn(aoBridge.clipboard, "writeText")
		.mockResolvedValue(undefined);
	const text = "完整 **答案**\n\n```ts\nconst x = 1;\n```";
	const earlier = "过程说明";
	render(
		<PeerTimeline
			deliveries={[
				{
					...a,
					replies: [
						{ id: "old", text: earlier },
						{ id: "new", text },
					],
				} as Delivery,
			]}
			label={() => "GP1"}
			provider={() => "codex"}
			status={() => "已回复"}
		/>,
	);
	const buttons = screen.getAllByRole("button", {
		name: "复制整条回复",
		hidden: true,
	});
	fireEvent.click(buttons.at(-1)!);
	await waitFor(() => expect(copy).toHaveBeenLastCalledWith(text));
	expect(screen.getByRole("button", { name: "已复制" })).toBeTruthy();
	fireEvent.click(buttons[0]);
	await waitFor(() => expect(copy).toHaveBeenLastCalledWith(earlier));
	copy.mockRestore();
});

it("keeps one card under the linked delivery as the task is rejected and accepted", () => {
	const client = new QueryClient();
	const task: components["schemas"]["PeerTask"] = {
		id: "task-1",
		goal: "修复登录",
		deliveryId: "a",
		owner: "one",
		state: "submitted",
		reviewer: "",
		commit: "a".repeat(40),
		revision: 2,
		evidence: "",
	};
	const deliveries = [
		a,
		{
			...a,
			id: "b",
			target: "two",
			replies: [{ ...a.replies![0], id: "r2", text: "独立复测说明" }],
		},
	];
	const view = (current: typeof task) => (
		<QueryClientProvider client={client}>
			<PeerTimeline
				roomId="room"
				deliveries={deliveries}
				tasks={[
					current,
					{
						...task,
						id: "legacy",
						goal: "历史提交",
						state: "accepted",
						deliveryId: undefined,
					},
				]}
				label={(id) => id}
				provider={() => undefined}
				status={() => "已回复"}
			/>
		</QueryClientProvider>
	);
	const { rerender } = render(view(task));
	const card = screen.getByRole("region", { name: "任务交付：修复登录" });
	const legacy = screen.getByRole("region", { name: "任务交付：历史提交" });
	expect(legacy.closest("article")).toBeNull();
	expect(legacy.closest('[aria-label="未关联回复的交付记录"]')).not.toBeNull();
	expect(card.closest("article")).toBe(
		screen.getByRole("article", { name: "来自 one 的回复" }),
	);
	expect(card).toHaveTextContent("待独立验收");
	expect(card.querySelector("details")).not.toHaveAttribute("open");
	rerender(
		view({
			...task,
			state: "claimed",
			reviewer: "two",
			reviewIssue: "登录失败",
		}),
	);
	expect(card).toHaveTextContent("验收未通过");
	expect(card).toHaveTextContent("登录失败");
	rerender(
		view({
			...task,
			state: "accepted",
			reviewer: "two",
			commit: "b".repeat(40),
			revision: 5,
		}),
	);
	expect(screen.getByRole("region", { name: "任务交付：修复登录" })).toBe(card);
	expect(card).toHaveTextContent("验收通过");
	expect(screen.getByText("我的分析")).toBeInTheDocument();
	expect(screen.getByText("独立复测说明")).toBeInTheDocument();
});

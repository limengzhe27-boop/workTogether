import { useRef, useState, useSyncExternalStore } from "react";
import { ImageIcon, X } from "lucide-react";
import { getApiBaseUrl, subscribeApiBaseUrl } from "../../lib/api-client";
import { mediaCrossOrigin } from "../../lib/file-media";
import { cn } from "../../lib/utils";
import { Dialog, DialogClose, DialogContent, DialogOverlay, DialogTitle, DialogTrigger } from "../ui/dialog";
import { Pressable } from "../ui/pressable";
import { ChatImageMenu } from "./ChatImage";

// Only generated artifacts served by this daemon qualify.
const generatedImagePath =
	/^\/api\/v1\/sessions\/[a-zA-Z0-9_.%-]+\/artifact-files\/generated-image-[a-f0-9]{64}\.(png|jpg|webp|gif)$/;

function ImagePending({ label }: { label: string }) {
	return (
		<div className="inline-flex max-w-full flex-col gap-2 text-sm text-muted-foreground">
			<div aria-hidden="true" className="generated-image-sheen aspect-[4/3] w-40 max-w-full rounded-xl bg-muted/65" />
			<span role="status">{label}</span>
		</div>
	);
}

function ImageNotice({ label, onRetry }: { label: string; onRetry?: () => void }) {
	return (
		<div className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground">
			<ImageIcon aria-hidden="true" className="size-4" />
			<span role="status">{label}</span>
			{onRetry && (
				<button type="button" className="rounded-md px-2 py-1 text-xs font-medium text-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" onClick={onRetry}>
					重试预览
				</button>
			)}
		</div>
	);
}

export function GeneratedImage({ url, name, status }: { url?: string; name?: string; status: string }) {
	const base = useSyncExternalStore(subscribeApiBaseUrl, getApiBaseUrl, getApiBaseUrl);
	if (status === "running") return <ImagePending label="正在生成图片…" />;
	if (status === "failed") return <ImageNotice label="图片生成失败" />;
	if (status === "cancelled") return <ImageNotice label="图片生成已中断" />;
	if (!url) return <ImageNotice label="本轮没有可预览的图片" />;
	if (!generatedImagePath.test(url)) return null;
	if (!base) return <ImagePending label="正在连接本机图片服务…" />;
	const src = base + url;
	return <ImagePreview key={src} src={src} label={name || "生成的图片"} />;
}

function ImagePreview({ src, label }: { src: string; label: string }) {
	const [attempt, setAttempt] = useState(0);
	const [loadState, setLoadState] = useState<"loading" | "ready" | "failed">("loading");
	const [open, setOpen] = useState(false);
	const thumbnail = useRef<HTMLButtonElement>(null);
	const url = attempt ? `${src}?preview=${attempt}` : src;
	const ready = loadState === "ready";

	if (loadState === "failed") {
		return <ImageNotice label="图片预览加载失败" onRetry={() => {
			setAttempt((previous) => previous + 1);
			setLoadState("loading");
		}} />;
	}

	return (
		<div className="relative max-w-full min-w-0">
			{!ready && <ImagePending label="正在加载图片…" />}
			<Dialog open={open} onOpenChange={setOpen}>
				<ChatImageMenu url={url} label={label}>
					<DialogTrigger asChild>
						<Pressable
							ref={thumbnail}
							aria-label={`查看大图：${label}`}
							disabled={!ready}
							className={cn(
								"max-w-full",
								ready
									? "generated-image-reveal block w-fit cursor-zoom-in rounded-xl focus-visible:ring-2 focus-visible:ring-ring"
									: "pointer-events-none absolute inset-0 size-full overflow-hidden opacity-0",
							)}
						>
							<img
								crossOrigin={mediaCrossOrigin(url)}
								src={url}
								alt={label}
								loading="lazy"
								decoding="async"
								onLoad={() => setLoadState("ready")}
								onError={() => setLoadState("failed")}
								className="block h-auto max-h-[min(22rem,55svh)] w-auto max-w-full rounded-xl"
							/>
						</Pressable>
					</DialogTrigger>
				</ChatImageMenu>
				<DialogContent
					overlay={<DialogOverlay className="generated-image-scrim" />}
					showCloseButton={false}
					aria-describedby={undefined}
					onCloseAutoFocus={(event) => {
						event.preventDefault();
						thumbnail.current?.focus({ preventScroll: true });
					}}
					onClick={(event) => {
						if (event.target === event.currentTarget) setOpen(false);
					}}
					className="generated-image-viewer inset-0 z-overlay grid size-full max-w-none cursor-zoom-out grid-cols-1 place-items-center translate-none rounded-none border-0 bg-transparent p-5 shadow-none sm:rounded-none"
				>
					<DialogTitle className="sr-only">{label}</DialogTitle>
					<ChatImageMenu url={url} label={label}>
						<img
							crossOrigin={mediaCrossOrigin(url)}
							src={url}
							alt={label}
							className="block h-auto max-h-[calc(100dvh-5rem)] w-auto max-w-full cursor-default select-none rounded-lg"
						/>
					</ChatImageMenu>
					<DialogClose aria-label="关闭大图" className="absolute right-4 top-4 grid size-9 cursor-pointer place-items-center rounded-full bg-black/40 text-white transition-colors hover:bg-black/70 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white">
						<X aria-hidden="true" className="size-[18px]" />
					</DialogClose>
				</DialogContent>
			</Dialog>
		</div>
	);
}

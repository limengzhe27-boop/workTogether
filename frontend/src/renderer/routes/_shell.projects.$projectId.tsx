import { SessionTabsTopbar, noDragStyle } from "../components/PageTopbar";
import { useSidebarChromeClearanceRef } from "../hooks/useSidebarChromeGeometry";
import { isMacPlatform, isLinuxPlatform } from "../lib/platform";
import { sidebarOccupiesLayout, useUiStore } from "../stores/ui-store";
import { cn } from "../lib/utils";
import { createFileRoute } from "@tanstack/react-router";
import { useNavigateToSession } from "../lib/navigate-to-session";
import { PeerRoom } from "../components/PeerRoom";
import { useWorkspaceQuery } from "../hooks/useWorkspaceQuery";
import { Button } from "../components/ui/button";
import { SessionsBoard } from "../components/SessionsBoard";

export const Route = createFileRoute("/_shell/projects/$projectId")({
	validateSearch: (search: Record<string, unknown>): { view?: "sessions" } =>
		search.view === "sessions" ? { view: "sessions" } : {},
	component: ProjectBoardRoute,
});

function ProjectBoardRoute() {
	const { projectId } = Route.useParams();
	const { view } = Route.useSearch();
	const navigate = Route.useNavigate();
	const openSession = useNavigateToSession();
	const isMac = isMacPlatform();
	const sidebarOpen = useUiStore(sidebarOccupiesLayout);
	const clearanceRef = useSidebarChromeClearanceRef<HTMLDivElement>(isMac);
	const room = view !== "sessions";
	const showSessions = () => void navigate({
		to: "/projects/$projectId", params: { projectId }, search: { view: "sessions" },
	});
	const workspace = useWorkspaceQuery({ includeCloud: false });
	const project = workspace.data?.find(
		(p) => p.id === projectId && !p.hostId && p.kind !== "cloud",
	);
	return (
		<div className="flex h-full min-h-0 flex-col">
			{project && (
				<SessionTabsTopbar
					clearanceRef={clearanceRef}
					clearanceClassName={cn(
						isMac && "session-topbar-titlebar-clearance-mac",
						!sidebarOpen &&
							isLinuxPlatform() &&
							"session-topbar-titlebar-clearance-linux",
					)}
					tabs={
						<div
							className="flex min-w-0 items-center gap-1 overflow-x-auto px-3"
							style={noDragStyle}
						>
							<Button
								variant="quiet"
								aria-pressed={room}
								onClick={() => void navigate({ to: "/projects/$projectId", params: { projectId }, search: {} })}
							>
								项目群聊
							</Button>
							<Button
								variant="quiet"
								aria-pressed={!room}
								onClick={showSessions}
							>
								会话看板
							</Button>
						</div>
					}
				/>
			)}
			{project && (
				<div hidden={!room} className={room ? "flex min-h-0 flex-1" : "hidden"}>
					<PeerRoom
						key={projectId}
						projectId={projectId}
						sessions={project.sessions}
						onManageSessions={showSessions}
						onOpenSession={(id) => openSession(projectId, id)}
					/>
				</div>
			)}
			{(!project || !room) && <SessionsBoard projectId={projectId} />}
		</div>
	);
}

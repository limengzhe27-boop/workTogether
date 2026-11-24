import { useQuery } from "@tanstack/react-query";
import { apiClient } from "../lib/api-client";

export function peerRoomKey(projectId: string) {
	return ["peer-room", `project-${projectId}`] as const;
}

export function usePeerRoom(projectId: string, enabled: boolean, poll = false) {
	const queryKey = peerRoomKey(projectId);
	const roomId = queryKey[1];
	return useQuery({
		queryKey,
		enabled,
		retry: false,
		refetchInterval: poll ? 3000 : false,
		queryFn: async () => {
			const result = await apiClient.GET("/api/v1/peer-rooms/{roomId}", {
				params: { path: { roomId } },
			});
			if (result.response.status === 404) return null;
			if (result.error || !result.data)
				throw result.error ?? new Error("房间读取失败");
			if (result.data.projectId !== projectId)
				throw new Error("房间与当前项目不匹配，已停止加载。");
			return result.data;
		},
	});
}

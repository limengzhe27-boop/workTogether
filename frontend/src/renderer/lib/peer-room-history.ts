import type { components } from "../../api/schema";

export type RoomDelivery = components["schemas"]["PeerDelivery"];

export function hasActiveRoomTurns(deliveries?: RoomDelivery[]) {
	return (
		deliveries?.some(
			(delivery) =>
				delivery.turnState === "running" ||
				delivery.turnState === "queued" ||
				delivery.replies?.some((reply) => reply.streaming),
		) ?? false
	);
}

import type { components } from "../../api/schema";
import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Loader2, Plus, Search } from "lucide-react";
import { apiErrorMessage, apiClient } from "../lib/api-client";
import { AgentAvatar } from "./AgentAvatar";
import { Button } from "./ui/button";
import { peerDisplayName } from "../lib/peer-display-name";
import type { WorkspaceSession } from "../types/workspace";

/** One explicit click creates one independent native session, without a task. */
export function PeerMemberPicker({ projectId, selectedCount, existingSessions = [], onAdded, onClose, onBack, onBusyChange }: {
 projectId: string;
 selectedCount: number;
 existingSessions?: WorkspaceSession[];
 onAdded: (ids: string[]) => Promise<string>;
 onClose: () => void;
 onBack: () => void;
 onBusyChange: (busy: boolean) => void;
}) {
 const cache = useQueryClient();
 const inFlight = useRef(false);
 const [creating, setCreating] = useState<string | null>(null);
 const [attempted, setAttempted] = useState(false);
 const [pendingMember, setPendingMember] = useState<{ id: string; label: string } | null>(null);
 const [notice, setNotice] = useState("");
 const [search, setSearch] = useState("");
 const catalog = useQuery({
  queryKey: ["peer-member-catalog"], retry: false,
  queryFn: async () => {
   const [readiness, settings] = await Promise.all([apiClient.GET("/api/v1/agents/readiness"), apiClient.GET("/api/v1/settings")]);
   if (readiness.error || !readiness.data || !Array.isArray(readiness.data.agents) || settings.error || !settings.data) throw new Error("无法读取客户端列表");
   return {agents: readiness.data.agents, supported: settings.data.chatHarnesses ?? []};
  },
 });

 const term = search.trim().toLowerCase();
 const existing = existingSessions.filter(session =>
  `${peerDisplayName(session)} ${session.provider}`.toLowerCase().includes(term),
 );
 const clients = catalog.data?.agents.filter(agent =>
  `${agent.label} ${agent.id}`.toLowerCase().includes(term),
 );
 async function add(id: string, label: string, reuse = false) {
  const retryingJoin = reuse && pendingMember?.id === id;
  if (inFlight.current || (attempted && !retryingJoin) || selectedCount >= 16) return;
  inFlight.current = true;
  setAttempted(true);
  setCreating(id);
  setNotice("");
  onBusyChange(true);
  let sessionId = reuse ? id : undefined;
  try {
   if (!sessionId) {
    const result = await apiClient.POST("/api/v1/sessions", {body: {projectId, kind:"worker", mode:"chat", harness:id as components["schemas"]["SpawnSessionRequest"]["harness"], clientRequestId:crypto.randomUUID(), prompt:""}});
    if (result.error || !result.data?.session.id) throw new Error(apiErrorMessage(result.error, "未收到创建结果，请核查会话列表"));
    sessionId = result.data.session.id;
    await cache.invalidateQueries({queryKey:["workspaces"]});
   }
   await onAdded([sessionId]);
   onClose();
  } catch (error) {
   if (sessionId) setPendingMember({ id: sessionId, label });
   setNotice(`${label}：${error instanceof Error ? error.message : "连接中断"}。${sessionId ? "会话已保留，可重试加入房间。" : "请核查会话列表。"}`);
  } finally {
   inFlight.current = false;
   setCreating(null);
   onBusyChange(false);
  }
 }
 return <section aria-label="添加成员" className="p-1">
  <div className="flex items-center gap-1 px-1 pb-1">
   <Button type="button" variant="quiet" size="icon-xs" aria-label="返回添加菜单" disabled={!!creating} onClick={onBack}><ArrowLeft className="size-3.5" /></Button>
   <span className="text-xs text-muted-foreground">添加 Agent</span>
  </div>
  <label className="mx-1 mb-1 flex items-center gap-2 rounded-md bg-secondary/50 px-2 py-1.5"><Search className="size-3.5 text-muted-foreground"/><input aria-label="搜索 Agent" value={search} onChange={e=>setSearch(e.target.value)} placeholder="搜索 Agent" className="min-w-0 flex-1 bg-transparent text-xs outline-none"/></label>
  {catalog.isPending && <p className="px-2 py-3 text-xs text-muted-foreground">正在读取客户端…</p>}
  {catalog.isError && <p role="alert" className="px-2 py-3 text-xs">无法读取客户端列表，请关闭后重试。</p>}
  <div className="max-h-60 overflow-y-auto">
  {existing.length > 0 && (
   <>
    <p className="px-2 pt-1 pb-0.5 text-[11px] text-muted-foreground">已有会话</p>
    {existing.map(session => {
     const name = peerDisplayName(session);
     return <button key={session.id} type="button" aria-label={`加入已有会话 ${name}`} disabled={selectedCount >= 16 || attempted || !!creating} onClick={() => void add(session.id, name, true)} className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left text-xs hover:bg-interactive-hover disabled:opacity-45 disabled:cursor-default">
      <AgentAvatar provider={session.provider} className="size-4" decorative />
      <span className="min-w-0 flex-1 truncate">{name}</span>
      {creating === session.id ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3 text-muted-foreground" />}
     </button>;
    })}
    {!!clients?.length && <p className="mt-1 border-t border-border/50 px-2 pt-2 pb-0.5 text-[11px] text-muted-foreground">新建会话</p>}
   </>
  )}
  {clients?.sort((a,b)=> {
   const rank=(agent: typeof a)=>catalog.data?.supported.includes(agent.id) ? agent.installation.state === "installed" ? 0 : 1 : 2;
   return rank(a)-rank(b) || (a.label || a.id).localeCompare(b.label || b.id);
  }).map(agent => {
   const supported = catalog.data?.supported.includes(agent.id);
   const missing = agent.installation.state === "not_installed";
   const reason = !supported ? "暂不支持群聊" : missing ? "未安装" : selectedCount >= 16 ? "成员已满" : "";
   const label = agent.label || agent.id;
   return <button key={agent.id} type="button" aria-label={`添加 ${label}`} disabled={!!reason || attempted || !!creating} onClick={()=>void add(agent.id,label)} className="flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left text-xs hover:bg-interactive-hover disabled:opacity-45 disabled:cursor-default">
    <AgentAvatar provider={agent.id} className="size-4" decorative />
    <span className="min-w-0 flex-1 truncate">{label}</span>
    {reason ? <span className="text-[10px] text-muted-foreground">{reason}</span> : creating === agent.id ? <Loader2 className="size-3.5 animate-spin" /> : <Plus className="size-3 text-muted-foreground" />}
   </button>;
  })}
  </div>
  {catalog.data && !existing.length && !clients?.length && <p className="px-2 py-3 text-xs text-muted-foreground">没有匹配的会话或客户端</p>}
  <details className="mt-1 border-t border-border/50 px-2 pt-2 pb-1 text-[11px] text-muted-foreground"><summary className="cursor-pointer">其他客户端</summary><p className="pt-2 leading-relaxed">列表来自平台已注册的客户端。未安装的客户端需要先安装；未列出的客户端需要接入原生会话适配器，不能仅填写名称或命令就加入。</p></details>
  {notice && (
   <div className="m-1 rounded-md bg-secondary/50 p-2 text-xs leading-relaxed break-words">
    <p role="status">{notice}</p>
    {pendingMember && (
     <Button
      type="button"
      variant="secondary"
      size="sm"
      className="mt-2"
      disabled={!!creating || selectedCount >= 16}
      onClick={() => void add(pendingMember.id, pendingMember.label, true)}
     >
      重试加入房间
     </Button>
    )}
   </div>
  )}
 </section>;
}

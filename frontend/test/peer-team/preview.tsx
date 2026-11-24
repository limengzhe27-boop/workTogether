/** Isolated UI fixture: no daemon, models, user sessions, or external network. */
import React from "react";
import { createRoot } from "react-dom/client";
import { QueryClient,QueryClientProvider } from "@tanstack/react-query";
import "../../src/renderer/styles.css";
const names:Record<string,string>={a:"GP1",b:"小迪",c:"GP2"};
const state:Record<string,string>={a:"running",b:"running",c:"completed"};
const decisions:Record<string,string>={};
window.fetch=async(input,options)=>{
 const req=input instanceof Request?input:new Request(input,options);
 const url=new URL(req.url);const id=url.pathname.match(/deliveries\/([^/]+)/)?.[1]??"";
 if(!url.pathname.startsWith("/api/v1/peer-rooms/fixture/"))return new Response("fixture denies network",{status:404});
 if(req.method==="POST"){
  const body=await req.json();if(body.action==="stop")state[id]="interrupted";if(body.action==="approve")decisions[id]=body.decision;
  return Response.json({status:body.action==="stop"?"stop_requested":body.action==="steer"?"steered":"decision_submitted"});
 }
 const base={kind:"activity",sequence:1,revision:1,createdAt:"2026-11-13T00:00:00Z",turnId:`turn-${id}`};
 return Response.json({sessionId:id,epoch:1,conversationId:`conv-${id}`,turnId:`turn-${id}`,providerTurnId:`native-${id}`,state:state[id],truncated:false,activities:[
 {...base,id:`cmd-${id}`,activityKind:"command",status:id==="b"?"failed":"completed",summary:`${names[id]} 的测试命令`,detail:{command:`echo '${names[id]}'`,cwd:"/isolated-fixture",exitCode:id==="b"?1:0,output:`${names[id]} 的独立输出\n${"sample output\n".repeat(8)}`}},
 ...(id==="a"?[{...base,sequence:2,id:"approval-a",activityKind:"approval",status:decisions.a?"resolved":"pending",summary:"测试审批（模拟）",requestId:"request-a",detail:{command:"fixture only",decisions:[{id:"deny",label:"拒绝"}]}}]:[])
 ]});
};
const {setApiBaseUrl}=await import("../../src/renderer/lib/api-client");
setApiBaseUrl(window.location.origin);
const {PeerTimeline}=await import("../../src/renderer/components/PeerTimeline");
createRoot(document.getElementById("root")!).render(<QueryClientProvider client={new QueryClient()}><main className="mx-auto max-w-3xl p-6"><h1 className="mb-5 text-sm">独立测试预览：全部数据与控制均为模拟，不连接真实 Agent。</h1><PeerTimeline roomId="fixture" label={id=>names[id]} provider={id=>id==="b"?"deepseek-harness":"codex"} status={d=>d.turnState||""} deliveries={Object.keys(names).map((id,i)=>({id,target:id,sender:"",epoch:1,state:"accepted",createdAt:"2026-11-13T00:00:00Z",message:"检查本轮运行记录",broadcastId:"fixture-round",repliesTruncated:false,replies:[{id:`reply-${id}`,text:`我是 ${names[id]}，这是我的答复。`,createdAt:`2026-11-13T00:00:0${id==="b"?1:i+2}Z`,streaming:false,turnState:"running"}]}))}/></main></QueryClientProvider>);

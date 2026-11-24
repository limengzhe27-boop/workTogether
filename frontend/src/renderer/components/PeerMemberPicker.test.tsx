import {render,screen,fireEvent,waitFor,cleanup} from "@testing-library/react";
import {QueryClient,QueryClientProvider} from "@tanstack/react-query";
import {beforeEach,afterEach,it,expect,vi} from "vitest";
import {PeerMemberPicker} from "./PeerMemberPicker";
const api=vi.hoisted(()=>({GET:vi.fn(),POST:vi.fn()}));
vi.mock("../lib/api-client",async original=>({...await original<typeof import("../lib/api-client")>(),apiClient:api}));
afterEach(cleanup);
beforeEach(()=>{vi.resetAllMocks();api.GET.mockImplementation(async(path:string)=>({data:path.endsWith('/settings')?{chatHarnesses:['codex','claude-code','agy']}:{agents:[{id:'codex',label:'Codex',installation:{state:'installed'}},{id:'claude-code',label:'Claude Code',installation:{state:'installed'}},{id:'agy',label:'Antigravity',installation:{state:'not_installed'}},{id:'terminal-only',label:'Terminal Only',installation:{state:'installed'}}]}}));});
function mount(count=3){const added=vi.fn().mockResolvedValue(''),close=vi.fn(),back=vi.fn();render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false}}})}><PeerMemberPicker projectId="p" selectedCount={count} onAdded={added} onClose={close} onBack={back} onBusyChange={vi.fn()}/></QueryClientProvider>);return {added,close,back};}
it('discovers registered clients and creates exactly one empty session per explicit selection',async()=>{api.POST.mockResolvedValue({data:{session:{id:'new'}}});const {added,close}=mount();const button=await screen.findByRole('button',{name:'添加 Claude Code'});expect(api.POST).not.toHaveBeenCalled();fireEvent.click(button);fireEvent.click(button);await waitFor(()=>expect(added).toHaveBeenCalledWith(['new']));expect(api.POST).toHaveBeenCalledTimes(1);expect(api.POST.mock.calls[0][1].body).toMatchObject({projectId:'p',harness:'claude-code',mode:'chat',prompt:''});expect(close).toHaveBeenCalled();expect(screen.queryByRole('spinbutton')).not.toBeInTheDocument();});
it('shows a rejection without retrying or closing',async()=>{api.POST.mockResolvedValue({error:{message:'Authentication required'}});const {close,added}=mount();fireEvent.click(await screen.findByRole('button',{name:'添加 Codex'}));await screen.findByRole('status');expect(screen.getByRole('status')).toHaveTextContent('Authentication required');fireEvent.click(screen.getByRole('button',{name:'添加 Codex'}));expect(api.POST).toHaveBeenCalledTimes(1);expect(added).not.toHaveBeenCalled();expect(close).not.toHaveBeenCalled();});
it("retries joining with the same session after room saving fails", async () => {
	api.POST.mockResolvedValue({ data: { session: { id: "new" } } });
	const { added, close } = mount();
	added.mockRejectedValueOnce(new Error("save failed"));
	fireEvent.click(await screen.findByRole("button", { name: "添加 Codex" }));
	expect(await screen.findByRole("status")).toHaveTextContent("会话已保留");
	expect(close).not.toHaveBeenCalled();

	const retry = screen.getByRole("button", { name: "重试加入房间" });
	expect(retry).toBeEnabled();
	fireEvent.click(retry);
	expect(added.mock.calls).toEqual([[["new"]], [["new"]]]);
	await waitFor(() => expect(close).toHaveBeenCalledTimes(1));
	expect(api.POST).toHaveBeenCalledTimes(1);
});
it('distinguishes missing installation from unsupported chat',async()=>{mount();expect(await screen.findByRole('button',{name:'添加 Antigravity'})).toBeDisabled();expect(screen.getByText('未安装')).toBeInTheDocument();expect(screen.getByRole('button',{name:'添加 Terminal Only'})).toBeDisabled();expect(screen.getByText('暂不支持群聊')).toBeInTheDocument();});
it('blocks capacity and supports returning without creating',async()=>{const {back}=mount(16);expect(await screen.findByRole('button',{name:'添加 Codex'})).toBeDisabled();fireEvent.click(screen.getByRole('button',{name:'返回添加菜单'}));expect(back).toHaveBeenCalled();expect(api.POST).not.toHaveBeenCalled();});
it('fails closed if capability discovery fails',async()=>{api.GET.mockResolvedValue({error:{message:'offline'}});mount();await screen.findByRole('alert');expect(screen.queryByRole('button',{name:'添加 Codex'})).not.toBeInTheDocument();expect(api.POST).not.toHaveBeenCalled();});

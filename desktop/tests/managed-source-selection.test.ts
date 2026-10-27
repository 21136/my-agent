import assert from "node:assert/strict";
import { test } from "node:test";
import { mountManagedSourceSelection } from "../src/shells/unified/managed-source-selection.ts";

class ElementPort {
  tagName: string; textContent = ""; value = ""; checked = false; disabled = false; hidden = false; open = false; type = "";
  style: Record<string,string> = {}; attributes = new Map<string,string>(); children: ElementPort[] = []; listeners = new Map<string,(event?:any)=>void>();
  constructor(tag: string) { this.tagName = tag; }
  setAttribute(k:string,v:string) { this.attributes.set(k,v); }
  getAttribute(k:string) { return this.attributes.get(k) ?? null; }
  appendChild(child:ElementPort) { this.children.push(child); return child; }
  append(...children:ElementPort[]) { this.children.push(...children); }
  replaceChildren(...children:ElementPort[]) { this.children = children; }
  addEventListener(type:string, listener:(event?:any)=>void) { this.listeners.set(type,listener); }
  dispatch(type:string) { this.listeners.get(type)?.({target:this}); }
  click() { this.dispatch("click"); }
  all():ElementPort[] { return [this,...this.children.flatMap(x=>x.all())]; }
}
const doc = { createElement:(tag:string)=>new ElementPort(tag) };
const sha = (n:string) => n.repeat(64);
const entry = (path:string, hash:string) => ({path,state:"raw_diverged",staged:false,rawDiverged:true,modeDiverged:false,workingSha256:sha(hash)});
const report:any = {
  format:1, mode:"managed_source_review_v1", review_digest:sha("a"), execution:false, readyForMerge:false, readyForLaunch:false,
  stable:{head:"stable-head",branch:"main",indexDigest:sha("b"),identity:{worktreeRoot:"stable"},inventory:{entries:[{...entry("desktop/file-0.ts","c"),state:"clean"}],excluded:[],limitations:[],inventory_digest:sha("d")}},
  sandbox:{head:"sandbox-head",branch:"experiment/sandbox",indexDigest:sha("e"),identity:{worktreeRoot:"sandbox"},inventory:{entries:Array.from({length:31},(_,i)=>entry(`desktop/file-${i}.ts`,"f")),excluded:[],limitations:[],inventory_digest:sha("1")}},
  history:{paths:[],commits:[]},effectiveDelta:{basis:"merge_base_to_sandbox_head",paths:[]},blockers:[],limitations:[]
};
const setup = (isDesktop=true, propose?:any, createCheckpoint?:any, executeMerge?:any) => { const root:any = new ElementPort("section"); root.ownerDocument=doc; return {root,panel:mountManagedSourceSelection(root,{isDesktop,propose,createCheckpoint,executeMerge})}; };

test("starts unselected and shows full sandbox/stable hashes and non-authority language", () => {
 const {root,panel}=setup(true,async()=>({ok:true,proposal:{}})); panel.show(report); const nodes=root.all();
 assert.equal(nodes.filter((n)=>n.type==="checkbox" && n.checked).length,0);
 assert.ok(nodes.some((n)=>n.textContent.includes(sha("f")))); assert.ok(nodes.some((n)=>n.textContent.includes(sha("c"))));
 assert.match(nodes.map(n=>n.textContent).join("\n"),/不代表.*归属|未验证.*归属/);
 assert.match(nodes.map(n=>n.textContent).join("\n"),/始终保留 stable/);
});

test("literal search and 25-row pagination retain search and checkbox identities",()=>{
 const {root,panel}=setup(); panel.show(report);
 const search=root.all().find(n=>n.getAttribute("aria-label")==="搜索 sandbox 路径"); const checks=root.all().filter(n=>n.type==="checkbox");
 assert.equal(checks.length,25); checks[0]!.checked=true; checks[0]!.dispatch("change");
 search!.value="file-2"; search!.dispatch("input");
 assert.strictEqual(root.all().find(n=>n.getAttribute("aria-label")==="搜索 sandbox 路径"),search);
 assert.ok(root.all().some(n=>n.textContent.includes("desktop/file-2.ts")));
 search!.value=""; search!.dispatch("input"); const same=root.all().find(n=>n.type==="checkbox"); assert.strictEqual(same,checks[0]); assert.equal(same!.checked,true);
 const next=root.all().find(n=>n.textContent==="下一页")!; next.click(); assert.ok(root.all().some(n=>n.textContent.includes("第 2/2 页")));
});

test("selection edits invalidate old proposal and stale async results cannot overwrite current state",async()=>{
 let resolve!:(value:any)=>void; const calls:any[]=[];
 const {root,panel}=setup(true,(request:any)=>{calls.push(request);return new Promise(r=>resolve=r);}); panel.show(report);
 root.all().find(n=>n.type==="checkbox")!.checked=true; root.all().find(n=>n.type==="checkbox")!.dispatch("change");
 const submit=root.all().find(n=>n.textContent==="核验所选范围（不合入）")!; submit.click();
 const second=root.all().find(n=>n.type==="checkbox")!; second.checked=true; second.dispatch("change");
 resolve({ok:true,proposal:{mode:"managed_source_selection_proposal_v1",execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]}}); await Promise.resolve(); await Promise.resolve();
 assert.equal(calls.length,1); assert.ok(!root.all().some(n=>n.textContent.includes("managed_source_selection_proposal_v1")));
 assert.deepEqual(Object.keys(calls[0]).sort(),["format","mode","reviewDigest","selectedPaths"].sort());
 assert.equal(calls[0].reviewDigest,sha("a"));
});

test("web/missing API disabled, clear and show suppress pending response",async()=>{
 const web=setup(false,async()=>({ok:true,proposal:{}})); web.panel.show(report); assert.equal(web.root.all().find(n=>n.textContent==="核验所选范围（不合入）")?.disabled,true);
 let resolve!:(value:any)=>void; const {root,panel}=setup(true,()=>new Promise(r=>resolve=r)); panel.show(report);
 const c=root.all().find(n=>n.type==="checkbox")!; c.checked=true;c.dispatch("change"); root.all().find(n=>n.textContent==="核验所选范围（不合入）")!.click();
 panel.clear(); resolve({ok:true,proposal:{mode:"managed_source_selection_proposal_v1",execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,secret:"should not render"}}); await Promise.resolve(); await Promise.resolve(); assert.equal(root.children.length,0);
});

test("malformed reports never expose selectable paths and failures use safe text",()=>{
 const {root,panel}=setup(true,async()=>({ok:false,message:"<img onerror=alert(1)>"})); panel.show({...report,review_digest:"bad"});
 assert.equal(root.all().some(n=>n.type==="checkbox"),false);
 assert.ok(root.all().every(n=>!("innerHTML" in n)));
});






test("missing desktop API stays disabled and rejected messages are rendered as text",async()=>{
 const noApi=setup(true); noApi.panel.show(report); assert.equal(noApi.root.all().find(n=>n.textContent==="核验所选范围（不合入）")?.disabled,true);
 const {root,panel}=setup(true,async()=>({ok:false,message:"<img onerror=alert(1)>拒绝"})); panel.show(report);
 const checkbox=root.all().find(n=>n.type==="checkbox")!; checkbox.checked=true; checkbox.dispatch("change");
 root.all().find(n=>n.textContent==="核验所选范围（不合入）")!.click(); await Promise.resolve(); await Promise.resolve();
 assert.ok(root.all().some(n=>n.textContent==="<img onerror=alert(1)>拒绝"));
 assert.ok(root.all().every(n=>!("innerHTML" in n)));
});

test("selection remains editable during pending and a late result cannot strand current submission",async()=>{
 const resolves:Array<(value:any)=>void>=[]; let calls=0;
 const {root,panel}=setup(true,()=>{calls++;return new Promise(resolve=>resolves.push(resolve));}); panel.show(report);
 const first=root.all().find(n=>n.type==="checkbox")!; first.checked=true; first.dispatch("change");
 const submit=root.all().find(n=>n.textContent==="核验所选范围（不合入）")!; submit.click();
 assert.equal(root.all().find(n=>n.type==="checkbox")?.disabled,false,"checkboxes remain editable while request is pending");
 const changed=root.all().find(n=>n.type==="checkbox")!; changed.checked=false; changed.dispatch("change");
 resolves[0]!({ok:true,proposal:{mode:"managed_source_selection_proposal_v1",reviewDigest:sha("a"),selectedPaths:["desktop/file-0.ts"],proposal_digest:sha("2"),execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]}});
 await Promise.resolve(); await Promise.resolve();
 assert.equal(root.all().find(n=>n.textContent==="核验所选范围（不合入）")?.disabled,true,"current empty selection is not stranded in pending");
 changed.checked=true; changed.dispatch("change"); assert.equal(root.all().find(n=>n.textContent==="核验所选范围（不合入）")?.disabled,false);
 root.all().find(n=>n.textContent==="核验所选范围（不合入）")!.click(); assert.equal(calls,2,"a fresh request can be submitted after the edit");
 resolves[1]!({ok:true,proposal:{mode:"managed_source_selection_proposal_v1",reviewDigest:sha("a"),selectedPaths:["desktop/file-0.ts"],proposal_digest:sha("2"),execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]}});
 await Promise.resolve(); await Promise.resolve();
});

test("proposal response must bind digest and exact selected paths before display",async()=>{
 for (const mutate of [
  (p:any)=>({...p,reviewDigest:sha("9")}),
  (p:any)=>({...p,selectedPaths:["desktop/file-1.ts"]}),
  (p:any)=>({...p,proposal_digest:"bad"}),
 ]) {
  const proposal={mode:"managed_source_selection_proposal_v1",reviewDigest:sha("a"),selectedPaths:["desktop/file-0.ts"],proposal_digest:sha("2"),execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]};
  const {root,panel}=setup(true,async()=>({ok:true,proposal:mutate(proposal)})); panel.show(report);
  const check=root.all().find(n=>n.type==="checkbox")!;check.checked=true;check.dispatch("change");root.all().find(n=>n.textContent==="核验所选范围（不合入）")!.click();await Promise.resolve();await Promise.resolve();
  assert.equal(root.all().some(n=>n.textContent.includes('"proposal_digest"')),false);
  assert.ok(root.all().some(n=>n.getAttribute("role")==="status"&&n.textContent.length>0));
 }
});


test("25 compact rows keep full hashes visible and complete metadata in closed text-only details", () => {
 const review = structuredClone(report);
 const metadata = { headOid:"1".repeat(40), indexOid:"2".repeat(40), workingOid:"3".repeat(40), headMode:"100644", indexMode:"100755", mode:"100755", size:12345, note:"<img src=x onerror=alert(1)>" };
 Object.assign(review.sandbox.inventory.entries[0], metadata);
 Object.assign(review.stable.inventory.entries[0], metadata, {size:54321});
 const {root,panel} = setup(); panel.show(review);
 const list = root.all().find(n=>n.getAttribute("aria-label")==="sandbox 路径选择")!;
 assert.equal(list.children.length,25);
 for (const [index,row] of list.children.entries()) {
  const details = row.children.find(n=>n.tagName==="details");
  assert.ok(details,"each row has a metadata disclosure");
  assert.equal(details.open,false,"metadata starts collapsed");
  assert.equal(details.children[0]?.tagName,"summary");
  assert.equal(details.children[0]?.textContent,"完整路径/索引/模式详情");
  const pre = details.children.find(n=>n.tagName==="pre"); assert.ok(pre);
  assert.equal(pre.style.whiteSpace,"pre-wrap"); assert.equal(pre.style.overflowWrap,"anywhere");
  const payload = JSON.parse(pre.textContent);
  assert.deepEqual(payload.sandbox,review.sandbox.inventory.entries[index]);
  if (index===0) assert.deepEqual(payload.stable,review.stable.inventory.entries[0]);
  const visible = row.children.filter(n=>n!==details).flatMap(n=>n.all()).map(n=>n.textContent).join("\n");
  assert.ok(visible.includes(sha("f")),"full sandbox SHA remains visible");
  assert.ok(!visible.includes('"workingOid"'),"full JSON is not printed outside the closed disclosure");
  if(index===0) { assert.ok(visible.includes(sha("c"))); assert.ok(visible.includes("clean")); }
  assert.ok(row.all().every(n=>!("innerHTML" in n)));
 }
 assert.ok(list.children[0]!.all().some(n=>n.tagName==="pre"&&n.textContent.includes("<img src=x onerror=alert(1)>")),"hostile metadata remains inert complete text");
});

test("checkpoint requires a current valid proposal and reports only the exact safe receipt",async()=>{
 const validProposal={mode:"managed_source_selection_proposal_v1",reviewDigest:sha("a"),selectedPaths:["desktop/file-0.ts"],proposal_digest:sha("2"),execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]};
 const receipt={format:1,mode:"managed_source_checkpoint_receipt_v1",reviewDigest:sha("a"),proposalDigest:sha("2"),checkpointDigest:sha("3"),selectedPaths:["desktop/file-0.ts"],rawBytes:7,storedBytes:7,checkpointCreated:true,execution:false,readyForMerge:false,readyForLaunch:false,nativeConsent:false};
 let calls=0; const {root,panel}=setup(true,async()=>({ok:true,proposal:validProposal}),async()=>{calls++;return {ok:true,receipt}}); panel.show(report);
 const save=()=>root.all().find(n=>n.textContent==="保存选定源码检查点（不合入）")!;
 assert.equal(save().disabled,true); const check=root.all().find(n=>n.type==="checkbox")!;check.checked=true;check.dispatch("change");
 root.all().find(n=>n.textContent==="核验所选范围（不合入）")!.click(); await Promise.resolve();await Promise.resolve(); assert.equal(save().disabled,false);
 save().click(); await Promise.resolve();await Promise.resolve(); assert.equal(calls,1);
 const output=root.all().map(n=>n.textContent).join("\n"); assert.match(output,/检查点 SHA-256/);assert.match(output,/7 字节/);assert.doesNotMatch(output,/bytesBase64|eA==/);assert.match(output,/不合入|不是合入/);
});

test("checkpoint is disabled without desktop API and renderer edits invalidate proposal and pending receipt",async()=>{
 const validProposal={mode:"managed_source_selection_proposal_v1",reviewDigest:sha("a"),selectedPaths:["desktop/file-0.ts"],proposal_digest:sha("2"),execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]};
 const noApi=setup(true,async()=>({ok:true,proposal:validProposal}));noApi.panel.show(report);assert.equal(noApi.root.all().find(n=>n.textContent==="保存选定源码检查点（不合入）")?.disabled,true);
 let resolve!:(value:any)=>void;const {root,panel}=setup(true,async()=>({ok:true,proposal:validProposal}),()=>new Promise(r=>resolve=r));panel.show(report);
 const check=root.all().find(n=>n.type==="checkbox")!;check.checked=true;check.dispatch("change");root.all().find(n=>n.textContent==="核验所选范围（不合入）")!.click();await Promise.resolve();await Promise.resolve();
 root.all().find(n=>n.textContent==="保存选定源码检查点（不合入）")!.click();assert.equal(check.disabled,false);check.checked=false;check.dispatch("change");
 resolve({ok:true,receipt:{format:1,mode:"managed_source_checkpoint_receipt_v1",reviewDigest:sha("a"),proposalDigest:sha("2"),checkpointDigest:sha("3"),selectedPaths:["desktop/file-0.ts"],rawBytes:7,storedBytes:7,checkpointCreated:true,execution:false,readyForMerge:false,readyForLaunch:false,nativeConsent:false}});await Promise.resolve();await Promise.resolve();
 assert.equal(root.all().some(n=>n.textContent.includes("检查点 SHA-256")),false);assert.equal(root.all().find(n=>n.textContent==="核验所选范围（不合入）")?.disabled,true);
});

test("malformed checkpoint receipts and cancellation never appear as success",async()=>{
 const validProposal={mode:"managed_source_selection_proposal_v1",reviewDigest:sha("a"),selectedPaths:["desktop/file-0.ts"],proposal_digest:sha("2"),execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]};
 const receipt={format:1,mode:"managed_source_checkpoint_receipt_v1",reviewDigest:sha("a"),proposalDigest:sha("2"),checkpointDigest:sha("3"),selectedPaths:["desktop/file-0.ts"],rawBytes:7,storedBytes:7,checkpointCreated:true,execution:false,readyForMerge:false,readyForLaunch:false,nativeConsent:false};
 for(const mutate of [(v:any)=>({...v,selectedPaths:["desktop/file-1.ts"]}),(v:any)=>({...v,nativeConsent:true}),(v:any)=>({...v,checkpointDigest:"BAD"}),(v:any)=>({...v,files:[{bytesBase64:"SECRET"}]})]){
  const {root,panel}=setup(true,async()=>({ok:true,proposal:validProposal}),async()=>({ok:true,receipt:mutate(receipt)}));panel.show(report);const check=root.all().find(n=>n.type==="checkbox")!;check.checked=true;check.dispatch("change");root.all().find(n=>n.textContent==="核验所选范围（不合入）")!.click();await Promise.resolve();await Promise.resolve();root.all().find(n=>n.textContent==="保存选定源码检查点（不合入）")!.click();await Promise.resolve();await Promise.resolve();assert.equal(root.all().some(n=>n.textContent.includes("检查点 SHA-256")),false);assert.ok(root.all().some(n=>n.getAttribute("role")==="status"&&n.textContent.length>0));assert.doesNotMatch(root.all().map(n=>n.textContent).join("\n"),/SECRET|bytesBase64/);
 }
 const {root,panel}=setup(true,async()=>({ok:true,proposal:validProposal}),async()=>({ok:false,message:"已取消"}));panel.show(report);const check=root.all().find(n=>n.type==="checkbox")!;check.checked=true;check.dispatch("change");root.all().find(n=>n.textContent==="核验所选范围（不合入）")!.click();await Promise.resolve();await Promise.resolve();root.all().find(n=>n.textContent==="保存选定源码检查点（不合入）")!.click();await Promise.resolve();await Promise.resolve();assert.equal(root.all().some(n=>n.textContent.includes("检查点 SHA-256")),false);
});


test("selected checkpoint enables only native selected merge and validates its receipt",async()=>{
 const proposal={mode:"managed_source_selection_proposal_v1",reviewDigest:sha("a"),selectedPaths:["desktop/file-0.ts"],proposal_digest:sha("2"),execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]};
 const checkpoint={format:1,mode:"managed_source_checkpoint_receipt_v1",reviewDigest:sha("a"),proposalDigest:sha("2"),checkpointDigest:sha("3"),selectedPaths:["desktop/file-0.ts"],rawBytes:7,storedBytes:7,checkpointCreated:true,execution:false,readyForMerge:false,readyForLaunch:false,nativeConsent:false};
 const merge={format:1,mode:"managed_selected_merge_receipt_v1",status:"source_merge_verified",planDigest:sha("4"),checkpointDigest:sha("3"),selectedPaths:["desktop/file-0.ts"],stableHeadBefore:"a".repeat(40),stableHeadAfter:"b".repeat(40),mergedTree:"c".repeat(40),sourceMergeExecuted:true,sourceHistoryMerged:false,readyForLaunch:false,receiptDigest:sha("7")};
 const requests:any[]=[];const {root,panel}=setup(true,async()=>({ok:true,proposal}),async()=>({ok:true,receipt:checkpoint}),async(request:any)=>{requests.push(request);return {ok:true,receipt:merge};});panel.show(report);
 const button=(label:string)=>root.all().find(n=>n.textContent===label)!; assert.equal(button("通过项目合入此检查点")?.disabled,true);
 const check=root.all().find(n=>n.type==="checkbox")!;check.checked=true;check.dispatch("change");button("核验所选范围（不合入）").click();await Promise.resolve();await Promise.resolve();button("保存选定源码检查点（不合入）").click();await Promise.resolve();await Promise.resolve();
 assert.equal(button("通过项目合入此检查点").disabled,false);button("通过项目合入此检查点").click();await Promise.resolve();await Promise.resolve();
 assert.deepEqual(requests,[{format:1,mode:"managed_selected_merge_request_v1",checkpointDigest:sha("3")}]);
 const status=root.all().find(n=>n.getAttribute("role")==="status")!.textContent;assert.match(status,/源码已合入/);assert.match(status,/未构建激活/);assert.match(status,/并非可使用验收/);
});

test("native selected merge never claims malformed receipts and panel edits suppress stale completion",async()=>{
 const proposal={mode:"managed_source_selection_proposal_v1",reviewDigest:sha("a"),selectedPaths:["desktop/file-0.ts"],proposal_digest:sha("2"),execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]};
 const checkpoint={format:1,mode:"managed_source_checkpoint_receipt_v1",reviewDigest:sha("a"),proposalDigest:sha("2"),checkpointDigest:sha("3"),selectedPaths:["desktop/file-0.ts"],rawBytes:7,storedBytes:7,checkpointCreated:true,execution:false,readyForMerge:false,readyForLaunch:false,nativeConsent:false};
 const malformed={format:1,mode:"managed_selected_merge_receipt_v1",status:"source_merge_verified",planDigest:"bad",checkpointDigest:sha("3"),selectedPaths:["desktop/file-0.ts"],stableHeadBefore:"a".repeat(40),stableHeadAfter:"b".repeat(40),mergedTree:"c".repeat(40),sourceMergeExecuted:true,sourceHistoryMerged:false,readyForLaunch:false,receiptDigest:sha("7")};
 let resolve!:(v:any)=>void;const {root,panel}=setup(true,async()=>({ok:true,proposal}),async()=>({ok:true,receipt:checkpoint}),()=>new Promise(r=>resolve=r));panel.show(report);
 const button=(label:string)=>root.all().find(n=>n.textContent===label)!;const check=root.all().find(n=>n.type==="checkbox")!;check.checked=true;check.dispatch("change");button("核验所选范围（不合入）").click();await Promise.resolve();await Promise.resolve();button("保存选定源码检查点（不合入）").click();await Promise.resolve();await Promise.resolve();button("通过项目合入此检查点").click();check.checked=false;check.dispatch("change");resolve({ok:true,receipt:malformed});await Promise.resolve();await Promise.resolve();
 assert.doesNotMatch(root.all().map(n=>n.textContent).join("\n"),/源码已合入/);
});


test("maintenance-required merge result permanently disables retry for the current checkpoint",async()=>{
 const proposal={mode:"managed_source_selection_proposal_v1",reviewDigest:sha("a"),selectedPaths:["desktop/file-0.ts"],proposal_digest:sha("2"),execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]};
 const checkpoint={format:1,mode:"managed_source_checkpoint_receipt_v1",reviewDigest:sha("a"),proposalDigest:sha("2"),checkpointDigest:sha("3"),selectedPaths:["desktop/file-0.ts"],rawBytes:7,storedBytes:7,checkpointCreated:true,execution:false,readyForMerge:false,readyForLaunch:false,nativeConsent:false};
 let calls=0;let resolveMerge!:(value:any)=>void;const {root,panel}=setup(true,async()=>({ok:true,proposal}),async()=>({ok:true,receipt:checkpoint}),async()=>{calls++;return new Promise(r=>resolveMerge=r);});panel.show(report);
 const button=(label:string)=>root.all().find(n=>n.textContent===label)!;const check=root.all().find(n=>n.type==="checkbox")!;check.checked=true;check.dispatch("change");button("核验所选范围（不合入）").click();await Promise.resolve();await Promise.resolve();button("保存选定源码检查点（不合入）").click();await Promise.resolve();await Promise.resolve();button("通过项目合入此检查点").click();assert.equal(calls,1);check.checked=false;check.dispatch("change");resolveMerge({ok:false,maintenanceRequired:true,message:"不得重试或启动"});await Promise.resolve();await Promise.resolve();
 panel.clear(); panel.show(report); const same=root.all().find(n=>n.type==="checkbox")!;same.checked=true;same.dispatch("change");button("核验所选范围（不合入）").click();await Promise.resolve();await Promise.resolve();button("保存选定源码检查点（不合入）").click();await Promise.resolve();await Promise.resolve();
 assert.equal(button("通过项目合入此检查点").disabled,true,"maintenance fence survives clear/show and identical checkpoint digest");button("通过项目合入此检查点").click();assert.equal(button("通过项目合入此检查点").disabled,true,"blocked checkpoint is disabled");assert.equal(calls,1,"handler guard refuses same-checkpoint invocation even if click is forced");
});


test("merge status preserves pending and safe refusal diagnostics over checkpoint summary",async()=>{
 const proposal={mode:"managed_source_selection_proposal_v1",reviewDigest:sha("a"),selectedPaths:["desktop/file-0.ts"],proposal_digest:sha("2"),execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]};
 const checkpoint={format:1,mode:"managed_source_checkpoint_receipt_v1",reviewDigest:sha("a"),proposalDigest:sha("2"),checkpointDigest:sha("3"),selectedPaths:["desktop/file-0.ts"],rawBytes:7,storedBytes:7,checkpointCreated:true,execution:false,readyForMerge:false,readyForLaunch:false,nativeConsent:false};
 let resolveMerge!:(value:any)=>void;const {root,panel}=setup(true,async()=>({ok:true,proposal}),async()=>({ok:true,receipt:checkpoint}),()=>new Promise(r=>resolveMerge=r));panel.show(report);
 const button=(label:string)=>root.all().find(n=>n.textContent===label)!;const status=()=>root.all().find(n=>n.getAttribute("role")==="status")!.textContent;const check=root.all().find(n=>n.type==="checkbox")!;check.checked=true;check.dispatch("change");button("核验所选范围（不合入）").click();await Promise.resolve();await Promise.resolve();button("保存选定源码检查点（不合入）").click();await Promise.resolve();await Promise.resolve();button("通过项目合入此检查点").click();assert.match(status(),/等待可信宿主原生确认/);
 resolveMerge({ok:false,code:"selected_stable_dirty",paths:["desktop/file-0.ts"],message:"源码范围被拒绝（selected_stable_dirty）：desktop/file-0.ts"});await Promise.resolve();await Promise.resolve();assert.match(status(),/selected_stable_dirty/);assert.match(status(),/desktop\/file-0\.ts/);
});

test("unknown merge outcomes are maintenance-fenced even after renderer epoch becomes stale",async()=>{
 const proposal={mode:"managed_source_selection_proposal_v1",reviewDigest:sha("a"),selectedPaths:["desktop/file-0.ts"],proposal_digest:sha("2"),execution:false,readyForMerge:false,readyForLaunch:false,checkpointCreated:false,nativeConsent:false,blockers:[]};
 const checkpoint={format:1,mode:"managed_source_checkpoint_receipt_v1",reviewDigest:sha("a"),proposalDigest:sha("2"),checkpointDigest:sha("3"),selectedPaths:["desktop/file-0.ts"],rawBytes:7,storedBytes:7,checkpointCreated:true,execution:false,readyForMerge:false,readyForLaunch:false,nativeConsent:false};
 for(const outcome of ["reject","malformed"]){let resolveMerge!:(value:any)=>void;let calls=0;const {root,panel}=setup(true,async()=>({ok:true,proposal}),async()=>({ok:true,receipt:checkpoint}),()=>{calls++;return new Promise((resolve,reject)=>{resolveMerge=(v)=>outcome==="reject"?reject(new Error("ipc lost")):resolve(v);});});panel.show(report);
  const button=(label:string)=>root.all().find(n=>n.textContent===label)!;const check=root.all().find(n=>n.type==="checkbox")!;check.checked=true;check.dispatch("change");button("核验所选范围（不合入）").click();await Promise.resolve();await Promise.resolve();button("保存选定源码检查点（不合入）").click();await Promise.resolve();await Promise.resolve();button("通过项目合入此检查点").click();check.checked=false;check.dispatch("change");resolveMerge(outcome==="malformed"?{ok:true,receipt:{}}:undefined);await Promise.resolve();await Promise.resolve();panel.clear();panel.show(report);const again=root.all().find(n=>n.type==="checkbox")!;again.checked=true;again.dispatch("change");button("核验所选范围（不合入）").click();await Promise.resolve();await Promise.resolve();button("保存选定源码检查点（不合入）").click();await Promise.resolve();await Promise.resolve();
  assert.equal(button("通过项目合入此检查点").disabled,true,`${outcome} outcome must fence identical digest`);assert.equal(calls,1);
 }
});

test("native merge confirmation rejects non-record checkpoint lanes and identities before dialog", async () => {
 const { readFileSync } = await import("node:fs");
 const { stripTypeScriptTypes } = await import("node:module");
 const main = readFileSync(new URL("../electron/main.ts", import.meta.url), "utf8");
 const handler = main.slice(main.indexOf("const managedSelectedMergeHandler ="));
 const callback = handler.slice(handler.indexOf("confirm: async ") + "confirm: ".length, handler.indexOf("\n  execute:")).trim().replace(/,$/, "");
 const request = { checkpointDigest: sha("3") };
 const plan = { checkpointDigest: sha("3"), selectedPaths: ["desktop/file-0.ts"], files: [{ path: "desktop/file-0.ts" }], stable: { worktreeRoot: "stable" }, sandbox: { worktreeRoot: "sandbox" } };
 const validReview = () => ({ stable: { identity: { worktreeRoot: "stable" } }, sandbox: { identity: { worktreeRoot: "sandbox" } } });
 const malformed = [undefined, null, false, "stable", 1, [], { identity: null }, { identity: [] }, { identity: "stable" }, { identity: {} }, { identity: { worktreeRoot: "wrong-root" } }];
 for (const lane of ["stable", "sandbox"]) {
  for (const { value, expectedDialogs } of [...malformed.map(value => ({ value, expectedDialogs: 0 })), { value: validReview()[lane], expectedDialogs: 1 }]) {
   const review: Record<string, unknown> = validReview(); review[lane] = value;
   let dialogs = 0;
   const parent = { isDestroyed: () => false };
   const checkpoint = { ...request, request: { selectedPaths: ["desktop/file-0.ts"] }, review };
   const confirm = new Function("workbenchWindow", "assertShellDevWorkbenchSender", "managedSelectedMergeCheckpoint", "dialog", "nativeSelectedMergeDetails", `return (${stripTypeScriptTypes(callback)});`)(parent, () => true, checkpoint, { showMessageBox: async () => { dialogs++; return { response: 1 }; } }, () => "details");
   assert.equal(await confirm({ caller: {}, request, plan }), false);
   assert.equal(dialogs, expectedDialogs, `${lane} must be a matching record identity`);
  }
 }
});

import assert from 'node:assert/strict';
import test from 'node:test';
import {summarizeConfirmPreview,renderConfirmCardHtml,isWorkCardRecallPreview,isConfirmChoiceAllowed,renderWorkCardRecallReadable} from '../src/shells/unified/confirm-preview.ts';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const sys='You are a read-only semantic recognizer. Compare the question directly against EVERY candidate supplied; the list is complete and must not be searched, reordered, shortened, or prefiltered. Return exactly one JSON object with exactly keys status and candidate_ids. Use match with exactly one candidate ID, ambiguous with two or more plausible IDs, or not_found with an empty list. Copy IDs verbatim from the supplied candidates. Do not invent IDs or add explanations. Never call tools, alter records, or infer/access any other session.';
const batchSystem='You are a read-only semantic recognizer. Compare the question with EVERY candidate in this batch only. Other batches may exist; do not infer their contents. Return exactly one JSON object with exactly keys status and candidate_ids. Use match with exactly one candidate ID, ambiguous with two or more plausible IDs, or not_found with an empty list. Copy IDs verbatim from this batch. Do not invent IDs, explain, call tools, alter records, or infer/access any other session.';
const req={model_id:'0x567-flash',vendor:'0x567',endpoint:'https://example.test/v1/chat/completions',session_id:'synthetic',provider_payload:{model:'gpt-6-luna',messages:[{role:'system',content:sys},{role:'user',content:JSON.stringify({question:'合成问题',candidates:[{id:'wc-'+'a'.repeat(64),title:'标题 <x>',excerpt:JSON.stringify({scope:'independent',items:[{title:'标题',requirements:['要求']}]})}]})}],temperature:0,stream:false,response_format:{type:'json_object'}}};
const raw='  '+JSON.stringify(req)+'\n';
test('exact recall preview is fully preserved and clearly identified',()=>{assert.equal(isWorkCardRecallPreview(raw),true);const v=summarizeConfirmPreview(raw);assert.equal(v.detail,raw);assert.match(v.reason,/当前会话问题/);assert.match(v.reason,/卡片标题和要求/);assert.match(v.reason,/0x567/);const html=renderConfirmCardHtml(raw,'<button>拒绝</button>');assert.match(html,/data-work-card-recall/);assert.match(html,/<details/);assert.ok(html.includes(raw.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')));});
test('legacy and project_id work-card excerpts both render, while malformed scopes fail closed',()=>{
 const item={title:'项目事项',requirements:['项目要求']};
 const makePreview=excerpt=>JSON.stringify({...req,provider_payload:{...req.provider_payload,messages:[req.provider_payload.messages[0],{role:'user',content:JSON.stringify({question:'找回项目卡',candidates:[{id:'wc-'+'c'.repeat(64),title:'项目卡',excerpt:JSON.stringify(excerpt)}]})}]}});
 const legacy=makePreview({scope:'project',items:[item]});
 const scoped=makePreview({scope:'project',project_id:'alpha',items:[item]});
 for(const preview of [legacy,scoped]){
  assert.equal(isWorkCardRecallPreview(preview),true);
  assert.match(renderConfirmCardHtml(preview,''),/data-work-card-recall/);
  assert.match(renderConfirmCardHtml(preview,''),/unified-recall-readable/);
 }
 for(const excerpt of [
  {scope:'project',project_id:'',items:[item]},
  {scope:'project',project_id:4,items:[item]},
  {scope:'project',project_id:'alpha',items:[item],unexpected:true},
  {scope:'project',items:[item],unexpected:true},
  {scope:'project'},
  {scope:'independent',project_id:'alpha',items:[item]},
  {scope:'project',items:[{title:'项目事项',requirements:['项目要求'],unexpected:true}]},
 ]) assert.equal(isWorkCardRecallPreview(makePreview(excerpt)),false,JSON.stringify(excerpt));
});

test('only valid specific card recall JSON is labelled',()=>{for(const value of ['{bad',JSON.stringify({...req,vendor:'other'}),JSON.stringify({...req,provider_payload:{...req.provider_payload,messages:[]}}),JSON.stringify({...req,provider_payload:{...req.provider_payload,messages:[req.provider_payload.messages[0],{role:'user',content:'{}'}]}}),JSON.stringify({...req,model_id:'wrong'})]){assert.equal(isWorkCardRecallPreview(value),false);assert.doesNotMatch(renderConfirmCardHtml(value,''),/data-work-card-recall/);}});
test('approval and approve-all require expansion, reject and ordinary confirmations unchanged',()=>{for(const choice of ['y','a']){assert.equal(isConfirmChoiceAllowed(raw,choice,false),false);assert.equal(isConfirmChoiceAllowed(raw,choice,true),true);assert.equal(isConfirmChoiceAllowed('ordinary',choice,false),true);}assert.equal(isConfirmChoiceAllowed(raw,'n',false),true);});
test('day and night submit share gate and detail toggle while previews scroll',()=>{const src=readFileSync(new URL('../src/shells/unified/index.ts',import.meta.url),'utf8');const css=readFileSync(new URL('../src/shells/unified/unified.css',import.meta.url),'utf8');assert.match(src,/isConfirmChoiceAllowed\(/);assert.match(src,/addEventListener\("toggle"/);assert.match(src,/data-work-card-recall/);assert.match(css,/\.unified-confirm-glass-card[^}]*max-height/s);assert.match(css,/\.unified-confirm-glass-preview[^}]*overflow:\s*auto/s);});

test('actual shared submit gate blocks keyboard and click paths until opened, but rejection remains available',()=>{
  const src=readFileSync(new URL('../src/shells/unified/index.ts',import.meta.url),'utf8');
  const start=src.indexOf('  function recallDetailsOpen(id: string)');
  const end=src.indexOf('  // ---- confirm clicks ----',start);
  assert.ok(start>=0&&end>start);
  const ts=createRequire(import.meta.url)('typescript');
  const section=ts.transpileModule(src.slice(start,end),{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.None}}).outputText;
  for(const preview of [raw,dialogueRawPreview])for(const perspective of ['day','night']){
    const sent=[];let status='';const handlers=[];
    class Details {open=false;classList={contains:(name)=>name==='unified-confirm-details'};closest(){return card;}}
    const detail=new Details();const yes={disabled:true,dataset:{id:'req',confirm:'y'}};const all={disabled:true,dataset:{id:'req',confirm:'a'}};
    const card={querySelectorAll(selector){return selector.includes('[data-id]')?[yes,all]:[yes,all];},querySelector(){return detail;}};
    const container={querySelectorAll(){return [card];},addEventListener(type,fn,capture){assert.equal(type,'toggle');assert.equal(capture,true);handlers.push(fn);}};
    const chat={model:{confirmOverlay:{requestId:'req',preview},blocks:[]},submitConfirm(id,choice){sent.push(['submit',id,choice]);return true;}};
    const client={sendConfirm(id,choice){sent.push(['send',id,choice]);}};
    const run=new Function('perspective','chatEl','confirmGlass','HTMLDetailsElement','chat','client','isConfirmChoiceAllowed','setStatus','renderChat','renderConfirmGlass','syncWorkingVisual',section+';return submitConfirmById;')(perspective,container,container,Details,chat,client,isConfirmChoiceAllowed,s=>{status=s;},()=>{},()=>{},()=>{});
    run('req','y');run('req','a');assert.equal(sent.length,0);assert.match(status,/展开/);
    run('req','n');assert.deepEqual(sent.map(x=>x[2]),['n','n']);sent.length=0;
    detail.open=true;handlers[0]({target:detail});assert.equal(yes.disabled,false);assert.equal(all.disabled,false);
    run('req','y');assert.deepEqual(sent.map(x=>x[2]),['y','y']);sent.length=0;
    detail.open=false;handlers[0]({target:detail});assert.equal(yes.disabled,true);run('req','y');assert.equal(sent.length,0);
  }
});

test('readable projection preserves all candidates, requirements and escapes hostile text',()=>{
 const many=[{id:'wc-'+'a'.repeat(64),title:'长中文'.repeat(130)+'<img src=x onerror=alert(1)>',excerpt:JSON.stringify({scope:'project',items:[{title:'第一事项',requirements:['第一要求\n第二行','long-english-'.repeat(100)]},{title:'第二事项',requirements:['<&"\u0000', '末项']}]})},{id:'wc-'+'b'.repeat(64),title:'候选二',excerpt:JSON.stringify({scope:'independent',items:[{title:'独立事项',requirements:['最后一条']}]})}];
 const question='问题 <script>alert(1)</script> '+ '无空格long'.repeat(80);
 const preview=JSON.stringify({...req,provider_payload:{...req.provider_payload,messages:[req.provider_payload.messages[0],{role:'user',content:JSON.stringify({question,candidates:many})}]}});
 const html=renderConfirmCardHtml(preview,'');
 assert.match(html,/unified-recall-readable/);assert.match(html,/接收地址/);assert.match(html,/原始预览/);assert.match(html,/不可替代原始预览/);
 const esc=s=>s.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;');
 for(const v of [question,...many.map(x=>x.title),'第一事项','第二事项','第一要求\n第二行','long-english-'.repeat(100),'<&"\u0000','末项','独立事项','最后一条'])assert.ok(html.includes(esc(JSON.stringify(v))),v);
 assert.ok(html.indexOf('第一事项')<html.indexOf('第二事项'));assert.ok(html.indexOf('第二事项')<html.indexOf('候选二'));assert.doesNotMatch(html,/<script>|<img /);
});

test('batch preview covers each batch, costs, unique cards and gating without eagerly rendering candidates',async()=>{
 const m=await import('../src/shells/unified/confirm-preview.ts');
 const first={...req.provider_payload,messages:[{...req.provider_payload.messages[0],content:batchSystem},req.provider_payload.messages[1]]};
 const second={...req.provider_payload,messages:[first.messages[0],{role:'user',content:JSON.stringify({question:'第二批问题',candidates:[{id:'wc-'+'b'.repeat(64),title:'第二批 <img>',excerpt:JSON.stringify({scope:'independent',items:[{title:'事项二',requirements:['最后要求']}]})}]})}]};
 const batch=JSON.stringify({model_id:req.model_id,vendor:req.vendor,endpoint:req.endpoint,session_id:req.session_id,provider_payloads:[first,second]});
 assert.equal(m.isWorkCardRecallPreview(batch),true);assert.equal(m.isConfirmChoiceAllowed(batch,'y',false),false);assert.equal(m.isConfirmChoiceAllowed(batch,'n',false),true);
 const html=m.renderConfirmCardHtml(batch,'');assert.match(html,/2\s*张/);assert.match(html,/2\s*批/);assert.match(html,/最多\s*2\s*次/);assert.match(html,/费用/);assert.equal((html.match(/data-recall-batch=/g)||[]).length,2);assert.doesNotMatch(html.split('<pre')[0],/事项二/);assert.ok(html.includes(batch.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')));
 const body=m.renderWorkCardRecallBatch(batch,1);assert.match(body,/第二批问题/);assert.match(body,/第二批 &lt;img&gt;/);assert.match(body,/事项二/);assert.match(body,/最后要求/);assert.equal(m.renderWorkCardRecallBatch(batch,2),'');
 const tooMany=JSON.stringify({model_id:req.model_id,vendor:req.vendor,endpoint:req.endpoint,session_id:req.session_id,provider_payloads:Array(33).fill(first)});assert.equal(m.isWorkCardRecallPreview(tooMany),false);
});

test('batch boundaries and bad later payload fail closed without affecting single preview',()=>{
 const first={...req.provider_payload,messages:[{...req.provider_payload.messages[0],content:batchSystem},req.provider_payload.messages[1]]};
 const envelope={model_id:req.model_id,vendor:req.vendor,endpoint:req.endpoint,session_id:req.session_id};
 assert.equal(isWorkCardRecallPreview(JSON.stringify({...envelope,provider_payloads:Array(32).fill(first)})),true);
 assert.equal(isWorkCardRecallPreview(JSON.stringify({...envelope,provider_payloads:[]})),false);
 assert.equal(isWorkCardRecallPreview(JSON.stringify({...envelope,provider_payloads:[first,{...first,model:'other'}]})),false);
 const huge={...first,messages:[first.messages[0],{role:'user',content:JSON.stringify({question:'q'.repeat(2*1024*1024),candidates:[{id:'wc-'+'a'.repeat(64),title:'t',excerpt:JSON.stringify({scope:'independent',items:[]})}]})}]};
 assert.equal(isWorkCardRecallPreview(JSON.stringify({...envelope,provider_payloads:[huge]})),false);
 assert.equal(isWorkCardRecallPreview(raw),true);
});
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

test('actual Python batch preview generator matches desktop recognition contract',()=>{
 const core=fileURLToPath(new URL('../../agent-core/',import.meta.url));
 const python=String.raw`
import sys,json
from types import SimpleNamespace
sys.path.insert(0,sys.argv[1])
import work_card_recall as r, work_records_luna_recall as luna
from work_card_ledger import WorkCardLedger
session=SimpleNamespace(conversation_id='synthetic-session',paths=None)
ledger=object.__new__(WorkCardLedger);ledger._session=session;ledger._store=None
entries=[]
for i in range(16):
 item='item-'+str(i)
 entries.append({'card_id':'wc-'+format(i,'064x'),'snapshot':{'session_id':session.conversation_id,'kind':'independent','completeness':'complete','requirement_count':1,'omitted_requirement_count':0,'card':{'session_id':session.conversation_id,'item_id':item,'title':'合成标题 '+str(i),'requirements':[{'session_id':session.conversation_id,'item_id':item,'text':'合成要求 '+str(i)}]}}})
WorkCardLedger.list_cards=lambda self: entries
luna.get_registry=lambda *_: SimpleNamespace(get=lambda _:SimpleNamespace(id=luna.MODEL_ID,vendor=luna.EXPECTED_VENDOR,provider_model=luna.EXPECTED_PROVIDER_MODEL,max_input_tokens=1000000,chat_completions_url=lambda:'https://example.invalid/v1/chat/completions'))
batch=r.prepare_batch_recall(ledger,entries,'合成问题',max_batch_bytes=1400)
single=r.prepare_recall(ledger,entries,'合成问题')
assert batch.status=='ready',batch.reason
assert single.status=='ready',single.reason
assert len(json.loads(batch.preview_json)['provider_payloads'])>1
print(json.dumps({'batch':batch.preview_json,'single':single.preview_json},ensure_ascii=False))
`;
 const env={SYSTEMROOT:process.env.SYSTEMROOT,WINDIR:process.env.WINDIR,PATH:process.env.PATH};
 const result=spawnSync('F:/my-agent-main/.venv/Scripts/python.exe',['-I','-B','-c',python,core],{encoding:'utf8',env,timeout:10000});
 assert.equal(result.status,0,result.stderr);const previews=JSON.parse(result.stdout);
 assert.equal(isWorkCardRecallPreview(previews.batch),true);assert.equal(isWorkCardRecallPreview(previews.single),true);
 assert.equal(isConfirmChoiceAllowed(previews.batch,'y',false),false);assert.equal(isConfirmChoiceAllowed(previews.batch,'n',false),true);
 const batch=JSON.parse(previews.batch);const wrong={...batch,provider_payloads:batch.provider_payloads.map((p,i)=>i?{...p,messages:[{...p.messages[0],content:'untrusted prompt'},p.messages[1]]}:p)};
 assert.equal(isWorkCardRecallPreview(JSON.stringify(wrong)),false);
 const swapped={...batch,provider_payloads:[{...batch.provider_payloads[0],messages:[JSON.parse(previews.single).provider_payload.messages[0],batch.provider_payloads[0].messages[1]]}]};
 assert.equal(isWorkCardRecallPreview(JSON.stringify(swapped)),false);
 const single=JSON.parse(previews.single),wrongSingle={...single,provider_payload:{...single.provider_payload,messages:[batch.provider_payloads[0].messages[0],single.provider_payload.messages[1]]}};
 assert.equal(isWorkCardRecallPreview(JSON.stringify(wrongSingle)),false);
});

const dialogueRawPreview=JSON.stringify({model_id:'0x567-flash',vendor:'0x567',endpoint:'https://example.test/v1/chat/completions',session_id:'synthetic-session',provider_payload:{model:'gpt-6-luna',messages:[{role:'system',content:sys},{role:'user',content:JSON.stringify({question:'What was the complete answer?',candidates:[{id:'msg-00000000',title:'user message 1',excerpt:'Question source'}, {id:'msg-00000003',title:'assistant message 4',excerpt:'Recovered full raw candidate <script>\n'+ 'untruncated-private-source-'.repeat(90)}]})}],temperature:0,stream:false,response_format:{type:'json_object'}}});
test('dialogue recall preview is strictly recognized, fully disclosed and requires details before approval',()=>{
 assert.equal(isWorkCardRecallPreview(dialogueRawPreview),true);
 const view=summarizeConfirmPreview(dialogueRawPreview);
 assert.equal(view.detail,dialogueRawPreview);
 assert.equal(view.headline,'\u5bf9\u8bdd\u539f\u6587\u8bed\u4e49\u56de\u67e5\uff1a\u5916\u53d1\u786e\u8ba4');
 assert.match(view.reason,/0x567/);
 assert.match(view.reason,/2/);
 assert.equal(view.compact,true);
 const html=renderConfirmCardHtml(dialogueRawPreview,'<button>Approve</button>');
 assert.match(html,/data-work-card-recall/);
 assert.match(html,/<details/);
 assert.match(html,/https:\/\/example\.test\/v1\/chat\/completions/);
 assert.match(html,/\u5b8c\u6574\u539f\u59cb\u9884\u89c8/);
 assert.match(html,/&lt;script&gt;/);
 assert.ok(html.includes(dialogueRawPreview.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')));
 const readable=renderWorkCardRecallReadable(dialogueRawPreview);
 for(const text of ['Question source','Recovered full raw candidate <script>\n'+ 'untruncated-private-source-'.repeat(90)]) assert.ok(readable.includes(JSON.stringify(text).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;').replaceAll('"','&quot;')));
 assert.equal(isConfirmChoiceAllowed(dialogueRawPreview,'y',false),false);
 assert.equal(isConfirmChoiceAllowed(dialogueRawPreview,'a',false),false);
 assert.equal(isConfirmChoiceAllowed(dialogueRawPreview,'y',true),true);
 assert.equal(isConfirmChoiceAllowed(dialogueRawPreview,'a',true),true);
 assert.equal(isConfirmChoiceAllowed(dialogueRawPreview,'n',false),true);
 const parsed=JSON.parse(dialogueRawPreview);
 const badSystem=structuredClone(parsed);badSystem.provider_payload.messages[0].content='not the frozen dialogue recall system';
 const badId=structuredClone(parsed);badId.provider_payload.messages[1].content=JSON.stringify({question:'q',candidates:[{id:'wc-'+'a'.repeat(64),title:'assistant message 4',excerpt:'x'}]});
 const badTitle=structuredClone(parsed);badTitle.provider_payload.messages[1].content=JSON.stringify({question:'q',candidates:[{id:'msg-00000003',title:'user message 5',excerpt:'x'}]});
 for(const bad of [JSON.stringify({...parsed,vendor:'other'}),JSON.stringify(badSystem),JSON.stringify(badId),JSON.stringify(badTitle)]) assert.equal(isWorkCardRecallPreview(bad),false);
 assert.equal(isWorkCardRecallPreview(raw),true,'existing work-card recall remains recognized');
});

test('dialogue confirmation recognizes the exact agent-core frozen system prompt',()=>{
 const core=fileURLToPath(new URL('../../agent-core/',import.meta.url));
 const script='import sys,json;sys.path.insert(0,sys.argv[1]);from work_records_luna_recall import _SYSTEM;print(json.dumps(_SYSTEM))';
 const result=spawnSync('F:/my-agent-main/.venv/Scripts/python.exe',['-I','-B','-c',script,core],{encoding:'utf8',timeout:10000});
 assert.equal(result.status,0,result.stderr);
 assert.equal(JSON.parse(result.stdout),sys);
 assert.equal(isWorkCardRecallPreview(dialogueRawPreview),true);
});

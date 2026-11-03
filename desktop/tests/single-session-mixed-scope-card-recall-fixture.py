import asyncio, json, os, pathlib, sys, threading, uuid
root=pathlib.Path(sys.argv[1]).resolve(); core=pathlib.Path(sys.argv[2]).resolve()
phase=sys.argv[3]
manifest=json.loads(sys.argv[4]) if len(sys.argv)>4 else None
assert pathlib.Path(os.environ["MY_AGENT_ROOT"]).resolve()==root
if phase == "seed":
 for name in ("data","workspace","evolve"): (root/name).mkdir(parents=True,exist_ok=True)
 (root/"evolve"/"_index.core.toml").write_text("[core]\n",encoding="utf-8")
sys.path.insert(0,str(core))
from paths import AgentPaths
from session import Session, SessionMeta
from work_records import WorkRecordStore
from work_card_ledger import WorkCardLedger
from work_record_cards import build_project_work_record_cards, build_independent_work_record_card
from work_records_experiment import WorkRecordExperimentController
from work_records_luna_recall import WorkRecordsLunaRecall
from llm_client import LLMClient
from llm_models import ModelEntry, ModelRegistry
from main import WorkRecordControllerBindings
import httpx, llm_client, llm_models, work_records_luna_recall, server
from websockets.asyncio.server import serve
paths=AgentPaths(root,root/"workspace",root/"data",root/"evolve")

def make_session(session_id,project_id=""):
 directory=paths.data/"sessions"/session_id
 directory.mkdir(parents=True,exist_ok=True)
 session=Session(session_id,directory,"synthetic integration session",SessionMeta(project_id=project_id),[],paths)
 session.save()
 return session

if phase == "seed":
 current=make_session("20261101-10000001","alpha")
 store=WorkRecordStore(current)
 alpha_item=store.create_item(title="Synthetic Alpha Orbit Work Card",scope={"kind":"project","project_id":"alpha"})
 store.add_requirement(alpha_item["item_id"],"Synthetic alpha requirement",source_ref={"kind":"project","id":"alpha"},event_id="synthetic-alpha-event")
 digest="a"*64
 created=store.create_item_with_requirement(
  title="多项目任务批次",scope={"kind":"independent"},requirement="Synthetic multi-project original request",
  event_id=f"worker-batch-original-{digest}",idempotency_key=f"worker-batch-original:{digest}")
 batch_item=created[0] if isinstance(created,tuple) else created
 project_requirements = (
  ("beta", "Synthetic beta requirement"),
  ("gamma", "Synthetic gamma requirement"),
  ("delta", "Synthetic delta requirement"),
  ("epsilon", "Synthetic epsilon requirement"),
 )
 for index, (project_id, requirement) in enumerate(project_requirements):
  store.add_requirement(batch_item["item_id"], requirement,
   source_ref={"kind":"project","id":project_id},
   event_id=f"worker-batch-task-{digest}-{index}",
   idempotency_key=f"worker-batch-task:{digest}:{index}")
 independent=store.create_item(title="Synthetic independent event",scope={"kind":"independent"})
 store.add_requirement(independent["item_id"],"Synthetic independent event detail",event_id="synthetic-independent-event")
 ledger=WorkCardLedger(current,store)
 alpha_saved=ledger.save_snapshot(str(uuid.uuid4()),build_project_work_record_cards(store,"alpha"))
 independent_saved=ledger.save_snapshot(str(uuid.uuid4()),build_independent_work_record_card(store,independent["item_id"]))
 assert alpha_saved.status==independent_saved.status=="saved"# A foreign-session card contains a high-entropy private marker. It is created
 # before instrumentation, then any later attempt to read that Session is counted.
 foreign=make_session("20261101-10000002","foreign-project")
 foreign_store=WorkRecordStore(foreign)
 foreign_item=foreign_store.create_item(title="FOREIGN-SESSION-PRIVATE",scope={"kind":"project","project_id":"foreign-project"})
 foreign_store.add_requirement(foreign_item["item_id"],"FOREIGN-SESSION-PRIVATE requirement",source_ref={"kind":"project","id":"foreign-project"},event_id="foreign-private-event")
 foreign_ledger=WorkCardLedger(foreign,foreign_store)
 foreign_saved=foreign_ledger.save_snapshot(str(uuid.uuid4()),build_project_work_record_cards(foreign_store,"foreign-project"))
 assert foreign_saved.status=="saved"

 async def seed_backend():
  async with serve(server.WsSessionHandler(paths).handle,"127.0.0.1",0,max_size=65536) as listener:
   port=listener.sockets[0].getsockname()[1]
   print(json.dumps({"phase":"seed","port":port,"session_id":current.conversation_id,
    "alpha_card_id":alpha_saved.card_id,"independent_card_id":independent_saved.card_id,
    "foreign_card_id":foreign_saved.card_id,"current_card_ids":[alpha_saved.card_id,independent_saved.card_id],
    "fixture":"synthetic records persisted before backend shutdown"}),flush=True)
   await asyncio.to_thread(sys.stdin.readline)
 asyncio.run(seed_backend())
 sys.exit(0)
elif phase == "serve":
 if not isinstance(manifest,dict) or manifest.get("phase") != "seed": raise RuntimeError("missing seed receipt")
 current=Session.load(paths,manifest["session_id"])
 foreign=Session.load(paths,"20261101-10000002")
 alpha_saved=type("Saved",(),{"card_id":manifest["alpha_card_id"]})()
 independent_saved=type("Saved",(),{"card_id":manifest["independent_card_id"]})()
 foreign_saved=type("Saved",(),{"card_id":manifest["foreign_card_id"]})()
 if current.conversation_id != "20261101-10000001": raise RuntimeError("reloaded session identity mismatch")
else: raise RuntimeError("unknown fixture phase")
foreign_reads={"count":0}
original_store_read=WorkRecordStore._read
def tracked_store_read(self):
 if self._conversation_id==foreign.conversation_id: foreign_reads["count"]+=1
 return original_store_read(self)
WorkRecordStore._read=tracked_store_read

secret="synthetic-only-never-real"
endpoint="https://synthetic.provider.invalid/v1/chat/completions"
entry=ModelEntry(id="0x567-flash",name="Synthetic fixture",vendor="0x567",base_url=endpoint,provider_model="gpt-6-luna",api_key=secret,max_input_tokens=1000000)
registry=ModelRegistry(models=(entry,),default_flash_id=entry.id,default_pro_id=entry.id,alias_map={})
llm_models.get_registry=lambda *_args,**_kwargs: registry
llm_client.get_registry=lambda *_args,**_kwargs: registry
work_records_luna_recall.get_registry=lambda *_args,**_kwargs: registry
ModelRegistry.load=classmethod(lambda _cls,*_args,**_kwargs: registry)
provider_report={"attempts":0,"validated_calls":0,"candidate_ids":[],"selected_card_id":None,"failure_categories":[]}
def write_report():
 provider_report["foreign_session_reads"]=foreign_reads["count"]
 (root/"provider-report.json").write_text(json.dumps(provider_report),encoding="utf-8")
write_report()
original_http_client=httpx.Client
def fixture_http_client(*args,**kwargs):
 def respond(request):
  provider_report["attempts"]+=1
  try:
   assert str(request.url)==entry.chat_completions_url()
   assert request.headers.get("authorization")=="Bearer "+secret
   payload=json.loads(request.content)
   assert payload.get("model")=="gpt-6-luna"
   body=json.loads(payload["messages"][1]["content"])
   assert body.get("question")=="请找回之前关于SYNTHETIC-ALPHA-ORBIT的工作卡"
   candidates=body.get("candidates")
   assert type(candidates) is list and len(candidates)==2
   ids=[candidate["id"] for candidate in candidates]
   assert set(ids)=={alpha_saved.card_id,independent_saved.card_id}
   assert all("FOREIGN-SESSION-PRIVATE" not in json.dumps(candidate,ensure_ascii=False) for candidate in candidates)
   provider_report["validated_calls"]+=1
   provider_report["candidate_ids"]=ids
   provider_report["selected_card_id"]=alpha_saved.card_id
   write_report()
   content=json.dumps({"status":"match","candidate_ids":[alpha_saved.card_id]},separators=(",",":"))
   return httpx.Response(200,json={"model":"gpt-6-luna","choices":[{"index":0,"finish_reason":"stop","message":{"role":"assistant","content":content}}]})
  except Exception as exc:
   provider_report["failure_categories"].append(type(exc).__name__)
   write_report()
   raise AssertionError("synthetic mock provider request did not match frozen fixture")
 return original_http_client(*args,transport=httpx.MockTransport(respond),**kwargs)
llm_client.httpx.Client=fixture_http_client

def factory(bound_session,agent):
 client=LLMClient(paths=paths)
 client.set_cancel_event(agent.cancel_event)
 bound_store=WorkRecordStore(bound_session)
 controller=WorkRecordExperimentController(agent=agent,store=bound_store,recognizer=lambda *_:{"action":"none"},semantic_matcher=WorkRecordsLunaRecall(client))
 return WorkRecordControllerBindings(recall=controller)

server.resume_desktop_or_create=lambda _paths: current
async def main():
 handler=server.WsSessionHandler(paths,work_record_controller_factory=factory)
 async with serve(handler.handle,"127.0.0.1",0,max_size=65536) as listener:
  port=listener.sockets[0].getsockname()[1]
  print(json.dumps({"phase":"serve","port":port,"session_id":current.conversation_id,
   "alpha_card_id":alpha_saved.card_id,
   "independent_card_id":independent_saved.card_id,"foreign_card_id":foreign_saved.card_id,
   "current_card_ids":[alpha_saved.card_id,independent_saved.card_id],
   "fixture":"reloaded persisted session; actual Agent/WsSessionHandler; controlled MockTransport"}),flush=True)
  await asyncio.to_thread(sys.stdin.readline)
asyncio.run(main())




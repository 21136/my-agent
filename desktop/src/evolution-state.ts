export type EvolutionProjection={
  evolution_id:string;conversation_id:string;state_revision:number;current_candidate_id:string|null;current_instance_id:string|null;
  pending_operation_ids:string[];unknown_operation_ids:string[];isolation_mode:"local_managed"|"protected_guest"|null;
  candidate_status:"not_run"|"starting"|"ready"|"failed"|"stopped"|"quarantined";prototype_control_verified:boolean;
  scripted_recovery_verified:boolean;agent_recovery_verified:boolean;independent_review:"incomplete"|"not_run"|"passed"|"failed";
};
export type EvolutionView={active:boolean;evolution_id:string|null;label:string;canStop:boolean;currentCandidate:string|null;openChecks:string[]};
const STATUSES=new Set(["not_run","starting","ready","failed","stopped","quarantined"]);
export function evolutionView(projection:EvolutionProjection|null):EvolutionView{
  if(!projection||typeof projection.evolution_id!=="string"||!projection.evolution_id)return{active:false,evolution_id:null,label:"未绑定演化任务",canStop:false,currentCandidate:null,openChecks:["尚未在当前会话绑定演化范围"]};
  const status=STATUSES.has(projection.candidate_status)?projection.candidate_status:"unknown";const openChecks:string[]=[];
  if(!projection.prototype_control_verified)openChecks.push("控制版本/来源尚未验证");if(!projection.scripted_recovery_verified)openChecks.push("脚本恢复尚未验证");if(!projection.agent_recovery_verified)openChecks.push("Agent 同会话恢复尚未验证");if(projection.independent_review!=="passed")openChecks.push("独立审查未通过");if(projection.unknown_operation_ids.length)openChecks.push(`${projection.unknown_operation_ids.length} 个操作结果未知`);
  const candidate=typeof projection.current_candidate_id==="string"?projection.current_candidate_id:null;const hasInstance=typeof projection.current_instance_id==="string"&&projection.current_instance_id.length>0;const canStop=hasInstance&&status!=="stopped";
  const label=status==="ready"?"候选运行中":status==="failed"?"候选失败":status==="stopped"?"候选已停止":status==="not_run"?"尚未运行":status==="starting"?"候选启动中":"候选状态待核验";
  return{active:true,evolution_id:projection.evolution_id,label,canStop,currentCandidate:candidate,openChecks};
}

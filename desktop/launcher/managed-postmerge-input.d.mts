import type {ManagedPostmergeSourceSnapshot} from './managed-postmerge-source.mjs';
export function materializeManagedPostmergeSource(input:{stableRoot:string;sandboxRoot:string;planDigest:string;operationId:string;gitExecutable:string}):{
 operationRoot:string;sourceRoot:string;sandboxRoot:string;snapshot:ManagedPostmergeSourceSnapshot;
 materialized:{format:1;mode:'managed_postmerge_materialized_v1';operationId:string;planDigest:string;snapshotDigest:string;sourceInventoryDigest:string;files:ManagedPostmergeSourceSnapshot['files'];rawBytes:number;readyForLaunch:false;receiptDigest:string};
};

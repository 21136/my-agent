import * as path from "node:path";
import { ContractError } from "./contracts";

function invalid(code: "invalid_request" | "permission_denied", message: string): never { throw new ContractError(code, message); }
function absolute(value: unknown, field: string): string {
  if (typeof value !== "string" || !path.isAbsolute(value) || value.includes("\0")) invalid("invalid_request", `${field} must be an absolute path`);
  return path.normalize(value);
}

export function candidateEnvironment(input: { profileRoot: string; runtimePath: string }): NodeJS.ProcessEnv {
  const profileRoot=absolute(input.profileRoot,"profileRoot");
  const runtimePath=absolute(input.runtimePath,"runtimePath");
  const runtimeDirectory=path.dirname(runtimePath);
  const tempRoot=path.join(profileRoot,"tmp");
  const appData=path.join(profileRoot,"appdata");
  const localAppData=path.join(profileRoot,"localappdata");
  const env:NodeJS.ProcessEnv={
    PATH:runtimeDirectory,
    TEMP:tempRoot,
    TMP:tempRoot,
    USERPROFILE:profileRoot,
    HOME:profileRoot,
    APPDATA:appData,
    LOCALAPPDATA:localAppData,
    MY_AGENT_CANDIDATE_PROFILE:profileRoot,
    LANG:"C.UTF-8",
    LC_ALL:"C.UTF-8",
  };
  for(const name of ["SystemRoot","WINDIR"] as const){
    const value=process.env[name];
    if(value&&path.isAbsolute(value))env[name]=value;
  }
  return env;
}

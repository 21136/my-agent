import path from 'node:path';
import { readSelectedMergeEvidence } from './managed-selected-merge-store.mjs';
import { pathToFileURL } from 'node:url';
import { managedNamespaceDigest, managedNamespacePath, prepareManagedNamespace, readManagedNamespace } from './managed-namespace.mjs';
import { projectNamespaceDigest, projectNamespacePath, prepareProjectNamespace, readProjectNamespace } from './project-namespace.mjs';
import { inspectManagedSources, revalidateManagedInspection } from './managed-source-plan.mjs';
import { inspectManagedReview, revalidateManagedReview } from './managed-review.mjs';
import { createHash } from 'node:crypto';

const USAGE = `Usage:
  node managed-cli.mjs --prepare <stableRoot> <sandboxRoot> <UUID>
  node managed-cli.mjs --check <installationRoot>
  node managed-cli.mjs --inspect <installationRoot>
  node managed-cli.mjs --prepare-project <stableRoot> <sandboxRoot> <UUID>
  node managed-cli.mjs --check-project <stableRoot> <sandboxRoot> <UUID>
  node managed-cli.mjs --inspect-project <stableRoot> <sandboxRoot> <UUID>
  node managed-cli.mjs --review-project <stableRoot> <sandboxRoot> <UUID>
  node managed-cli.mjs --capture-selected-postmerge <stableRoot> <sandboxRoot> <planDigest> <absoluteGitExe>
  node managed-cli.mjs --materialize-selected-postmerge <stableRoot> <sandboxRoot> <planDigest> <operationUUID> <absoluteGitExe>
  node managed-cli.mjs --build-selected-postmerge <sandboxRoot> <operationUUID>
  node managed-cli.mjs --seal-selected-postmerge <sandboxRoot> <operationUUID>
  node managed-cli.mjs --verify-selected-release <sandboxRoot> <operationUUID> <releaseId>
  node managed-cli.mjs --prepare-production-context <stableRoot> <sandboxRoot> <installationUUID> <operationUUID> <releaseId>
  node managed-cli.mjs --check-production-context <stableRoot> <sandboxRoot> <installationUUID> <operationUUID> <releaseId>
  node managed-cli.mjs --check-production-runtime <stableRoot> <sandboxRoot> <installationUUID> <operationUUID> <releaseId>
  node managed-cli.mjs --help

This CLI prepares empty private or project-local namespaces and performs strictly read-only checks. Project-local mode provides logical separation only; credentials are forbidden.
--inspect never writes namespace state; its complete report is returned by the API.
--review-project returns the full namespace-bound read-only review JSON envelope; it never writes, confirms, adopts, or launches.
--capture-selected-postmerge reads a completed durable v2 operation and checks actual merged committed source. It returns metadata only; it does not build, seal, persist a snapshot, or advance installation state.
--materialize-selected-postmerge, --build-selected-postmerge and --seal-selected-postmerge write bounded inert artifacts in the sandbox only. Build uses that sandbox's existing desktop toolchain. Verify is read-only. None advances namespace state or permits launch.
--prepare-production-context stages a verified release and read-only binding in its registered project namespace. --check-production-context revalidates that binding and installed payload. They do not register runtimes, acquire a data lease, initialize data, activate, or launch.
--check-production-runtime verifies an existing explicit runtime registration and its bound runtime bytes. It neither registers a runtime nor starts a process; it grants no data lease or activation.
It never activates a runtime: ready_for_launch: false (always). --launch and --adopt
are rejected. Exit codes: 0 = prepare/check success or inspect with no blockers; 3 = report
returned with blocker codes but inspect is not ready; 1 = safety or I/O refusal; 2 = usage/unsupported command.
A successful --check may return code 0 while --inspect reports safety blockers. --review-project also returns code 3 when blocked.
`;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
const TRUSTED_POSTMERGE_GIT = process.platform === 'win32' ? 'C:\\Program Files\\Git\\cmd\\git.exe' : '/usr/bin/git';
const main = process.argv[1] && pathToFileURL(path.resolve(process.argv[1])).href === import.meta.url;

function emit(write, value) { write(typeof value === 'string' ? value : JSON.stringify(value)); }
function safeErrorCode(error) {
  const code = String(error?.code ?? '').toUpperCase();
  const known = new Set(['EEXIST', 'ENOENT', 'EACCES', 'EPERM', 'EINVAL', 'ENOTDIR', 'ELOOP', 'SOURCE_INSPECTION_INVALID', 'SOURCE_IDENTITY_MISSING', 'SOURCE_ROOT_MISMATCH', 'SOURCE_IDENTITY_BLOCKED', 'NAMESPACE_IDENTITY_MISMATCH']);
  if (known.has(code)) return code;
  const message = String(error?.message ?? '').toLowerCase();
  if (/managed_production_context_invalid:/.test(message)) return 'PRODUCTION_CONTEXT_REFUSED';
  if (/managed_runtime_registration_invalid:/.test(message)) return 'PRODUCTION_RUNTIME_REFUSED';
  if (/managed_toolchain_read_guard_invalid:/.test(message)) return 'TOOLCHAIN_READ_GUARD_REFUSED';
  if (/managed_build_invalid:.*(?:maintenance|busy lock|partial)|existing or partial build artifact/.test(message)) return 'POSTMERGE_MAINTENANCE_REQUIRED';
  if (/managed_postmerge_(?:input|build|build_child|release|source)_invalid:|managed_build_invalid:/.test(message)) return 'POSTMERGE_ARTIFACT_REFUSED';
  if (/different common git director|common git director/.test(message)) return 'SOURCE_COMMON_DIR_MISMATCH';
  if (/sandbox branch|detached/.test(message)) return 'SOURCE_BRANCH_INVALID';
  if (/root.*(absolute|missing|directory|git worktree)|not the git worktree root/.test(message)) return 'SOURCE_ROOT_INVALID';
  if (/ntfs|volume/.test(message)) return 'VOLUME_UNSAFE';
  if (/acl|access control|trusted owner/.test(message)) return 'ACL_UNSAFE';
  if (/reparse|symbolic|symlink|alias/.test(message)) return 'LINK_UNSAFE';
  if (/already exists|refusing reuse/.test(message)) return 'NAMESPACE_EXISTS';
  if (/marker|layout|empty data|data store/.test(message)) return 'NAMESPACE_INVALID';
  if (/inspection drift|snapshot drift/.test(message)) return 'SOURCE_INSPECTION_DRIFT';
  if (/git .*failed safely/.test(message)) return 'SOURCE_GIT_PREFLIGHT_FAILED';
  return 'PREFLIGHT_FAILED';
}
function blockerCodes(report) {
  const blockers = Array.isArray(report?.blockers) ? report.blockers : [];
  return blockers.map((item) => typeof item === 'string' ? item : item?.code).filter((code) => typeof code === 'string').slice(0, 64);
}
function assertSourceIdentity(report, stableRoot, sandboxRoot) {
  if (!report || report.format !== 1 || report.mode !== 'managed_source_inspection_v1' || !Array.isArray(report.blockers) || !/^[a-f0-9]{64}$/i.test(report.snapshot_digest ?? '') || !report.committedRange || !Array.isArray(report.committedRange.commits) || !Array.isArray(report.committedRange.paths) || !report.blockers.every((item) => item && typeof item.code === 'string' && Array.isArray(item.paths))) {
    throw Object.assign(new Error('source inspection contract invalid'), { code: 'SOURCE_INSPECTION_INVALID' });
  }
  const stable = report.stable;
  const sandbox = report.sandbox;
  if (!stable?.identity || !sandbox?.identity || typeof stable.identity.worktreeRoot !== 'string' || typeof sandbox.identity.worktreeRoot !== 'string') {
    throw Object.assign(new Error('source identities unavailable'), { code: 'SOURCE_IDENTITY_MISSING' });
  }
  if (path.resolve(stable.identity.worktreeRoot) !== path.resolve(stableRoot) || path.resolve(sandbox.identity.worktreeRoot) !== path.resolve(sandboxRoot)) {
    throw Object.assign(new Error('source roots differ from requested roots'), { code: 'SOURCE_ROOT_MISMATCH' });
  }
  const codes = blockerCodes(report).map((code) => code.toLowerCase());
  // Dirty-scope ambiguity is a merge/review blocker, not a reason to prevent
  // creation of a separate, empty namespace. Identity and repository safety are hard gates.
  // A deliberately incomplete content inventory blocks scope review, not empty namespace preparation.
  const hardBlocker = codes.find((code) => code !== 'complete_worktree_inventory_unavailable' && /root|branch|common.?dir|repository|worktree|identity|detached|path.?alias|source.?overlap/.test(code));
  if (hardBlocker) throw Object.assign(new Error('source identity or repository preflight blocked'), { code: 'SOURCE_IDENTITY_BLOCKED' });
}
function conciseInspection(report) {
  return {
    state: 'inspected',
    snapshot_digest: report.snapshot_digest,
    blocker_count: report.blockers.length,
    blocker_codes: blockerCodes(report),
    ready_for_launch: false,
  };
}

export async function runManagedCli(args = process.argv.slice(2), { write = console.log, namespaceDependencies, projectNamespaceDependencies } = {}) {
  if (!Array.isArray(args) || typeof write !== 'function') {
    emit(write ?? console.log, { state: 'refused', code: 'INVALID_ARGUMENTS', ready_for_launch: false });
    return { code: 2, ready_for_launch: false };
  }
  if (args.length === 1 && args[0] === '--help') {
    emit(write, USAGE);
    return { code: 0, ready_for_launch: false };
  }
  if (args.some((arg) => ['--launch', '--adopt'].includes(arg))) {
    emit(write, { state: 'refused', code: 'COMMAND_NOT_SUPPORTED', ready_for_launch: false });
    return { code: 2, ready_for_launch: false, error_code: 'COMMAND_NOT_SUPPORTED' };
  }
  try {
    if (args[0]==='--check-production-runtime') {
      const valid=args.length===6 && args.slice(1).every(value=>typeof value==='string')
        && args.slice(1,3).every(value=>path.isAbsolute(value)&&path.resolve(value)===value)
        && args.slice(3,5).every(value=>UUID.test(value)&&value===value.toLowerCase())
        && /^[a-f0-9]{64}$/.test(args[5]);
      if(!valid){emit(write,{state:'refused',code:'INVALID_USAGE',ready_for_launch:false});return {code:2,error_code:'INVALID_USAGE',ready_for_launch:false};}
      const [,stableRoot,sandboxRoot,installationId,operationId,releaseId]=args;
      const {readManagedRuntimeRegistration}=await import('./managed-runtime-registration.mjs');
      const {descriptor}=await readManagedRuntimeRegistration({stableRoot,sandboxRoot,installationId,operationId,releaseId});
      emit(write,{state:'production_runtime_verified',installation_id:installationId,operation_id:operationId,release_id:releaseId,binding_digest:descriptor.bindingDigest,runtime_digest:descriptor.runtimeDigest,runtime_registered:true,runtime_bytes_checked:true,writer_lease:false,ready_for_launch:false});
      return {code:0,descriptor,ready_for_launch:false};
    }
    if (['--prepare-production-context','--check-production-context'].includes(args[0])) {
      const valid=args.length===6 && args.slice(1).every(value=>typeof value==='string')
        && args.slice(1,3).every(value=>path.isAbsolute(value)&&path.resolve(value)===value)
        && args.slice(3,5).every(value=>UUID.test(value)&&value===value.toLowerCase())
        && /^[a-f0-9]{64}$/.test(args[5]);
      if(!valid){emit(write,{state:'refused',code:'INVALID_USAGE',ready_for_launch:false});return {code:2,error_code:'INVALID_USAGE',ready_for_launch:false};}
      const [command,stableRoot,sandboxRoot,installationId,operationId,releaseId]=args;
      const {prepareManagedProductionContext,readManagedProductionContext}=await import('./managed-production-context.mjs');
      const input={stableRoot,sandboxRoot,installationId,operationId,releaseId};
      const checked=command==='--check-production-context';
      const result=checked?await readManagedProductionContext(input):await prepareManagedProductionContext(input);
      const descriptor=checked?result.descriptor:result;
      emit(write,{state:checked?'production_context_verified':'production_context_prepared',installation_id:installationId,operation_id:operationId,release_id:releaseId,binding_digest:descriptor.bindingDigest,runtime_registered:checked?result.runtimeRegistered===true:false,runtime_bytes_checked:false,writer_lease:false,ready_for_launch:false});
      return {code:0,descriptor,ready_for_launch:false};
    }
    if (['--materialize-selected-postmerge','--build-selected-postmerge','--seal-selected-postmerge','--verify-selected-release'].includes(args[0])) {
      const command=args[0], materialize=command==='--materialize-selected-postmerge', verify=command==='--verify-selected-release';
      const valid=materialize
        ? args.length===6 && args.slice(1).every(value=>typeof value==='string') && path.isAbsolute(args[1]) && path.isAbsolute(args[2]) && /^[a-f0-9]{64}$/.test(args[3]) && UUID.test(args[4]) && args[4]===args[4].toLowerCase() && args[5]===TRUSTED_POSTMERGE_GIT
        : args.length===(verify?4:3) && typeof args[1]==='string' && path.isAbsolute(args[1]) && typeof args[2]==='string' && UUID.test(args[2]) && args[2]===args[2].toLowerCase() && (!verify||/^[a-f0-9]{64}$/.test(args[3]));
      if(!valid){emit(write,{state:'refused',code:'INVALID_USAGE',ready_for_launch:false});return {code:2,ready_for_launch:false,error_code:'INVALID_USAGE'};}
      if(materialize){
        const [,stableRoot,sandboxRoot,planDigest,operationId,gitExecutable]=args;
        const {materializeManagedPostmergeSource}=await import('./managed-postmerge-input.mjs');
        const operation=materializeManagedPostmergeSource({stableRoot,sandboxRoot,planDigest,operationId,gitExecutable});
        emit(write,{state:'postmerge_source_materialized',operation_id:operationId,snapshot_digest:operation.snapshot.snapshotDigest,source_file_count:operation.snapshot.files.length,ready_for_launch:false});
        return {code:0,ready_for_launch:false};
      }
      const [,sandboxRoot,operationId,releaseId]=args;
      if(command==='--build-selected-postmerge'){
        const {buildManagedPostmerge}=await import('./managed-postmerge-build.mjs');
        const {receipt}=await buildManagedPostmerge({sandboxRoot,operationId,toolchainRoot:path.join(sandboxRoot,'desktop')});
        emit(write,{state:'postmerge_build_verified',operation_id:operationId,receipt_digest:receipt.receiptDigest,output_count:receipt.outputs.length,build_verified:true,ready_for_launch:false});
        return {code:0,ready_for_launch:false};
      }
      const {sealManagedPostmergeRelease,verifyManagedPostmergeRelease}=await import('./managed-postmerge-release.mjs');
      const descriptor=verify?verifyManagedPostmergeRelease({sandboxRoot,operationId,releaseId}):sealManagedPostmergeRelease({sandboxRoot,operationId});
      emit(write,{state:verify?'postmerge_release_verified':'postmerge_release_sealed',operation_id:operationId,release_id:descriptor.releaseId,ready_for_launch:false});
      return {code:0,descriptor,ready_for_launch:false};
    }
    if (args[0] === '--capture-selected-postmerge') {
      if (args.length !== 5 || !args.slice(1).every(value => typeof value === 'string')
          || !path.isAbsolute(args[1]) || !path.isAbsolute(args[2]) || args[4] !== TRUSTED_POSTMERGE_GIT
          || !/^[a-f0-9]{64}$/.test(args[3])) {
        emit(write, { state: 'refused', code: 'INVALID_USAGE', ready_for_launch: false });
        return { code: 2, ready_for_launch: false, error_code: 'INVALID_USAGE' };
      }
      const [, stableRoot, sandboxRoot, planDigest, gitExecutable] = args;
      const {plan,receipt} = readSelectedMergeEvidence({sandboxRoot,planDigest});
      const {captureManagedPostmergeSource} = await import('./managed-postmerge-source.mjs');
      const snapshot = await captureManagedPostmergeSource({stableRoot,sandboxRoot,plan,receipt,gitExecutable});
      emit(write, {state:'postmerge_source_captured',plan_digest:planDigest,snapshot_digest:snapshot.snapshotDigest,
        source_file_count:snapshot.files.length,ready_for_launch:false,build_verified:false});
      return {code:0,snapshot,ready_for_launch:false,build_verified:false};
    }
    if (args.length === 4 && args[0] === '--prepare' && args.slice(1).every((arg) => typeof arg === 'string' && arg.length > 0) && UUID.test(args[3])) {
      const [, stableRoot, sandboxRoot, installationId] = args;
      const inspection = await inspectManagedSources({ stableRoot, sandboxRoot });
      assertSourceIdentity(inspection, stableRoot, sandboxRoot);
      const namespace = await prepareManagedNamespace({
        localAppData: namespaceDependencies?.localAppData ?? process.env.LOCALAPPDATA,
        installationId,
        stableSourceRoot: stableRoot,
        sandboxSourceRoot: sandboxRoot,
      }, namespaceDependencies);
      emit(write, { state: 'prepared_empty', installation_id: namespace.installation_id, namespace_digest: managedNamespaceDigest(namespace), source_blocker_count: inspection.blockers.length, ready_for_launch: false });
      return { code: 0, namespace, inspection, ready_for_launch: false };
    }
    if (args.length === 4 && ['--prepare-project', '--check-project', '--inspect-project'].includes(args[0]) && args.slice(1).every((arg) => typeof arg === 'string' && arg.length > 0) && UUID.test(args[3]) && path.isAbsolute(args[1]) && path.isAbsolute(args[2])) {
      const [command, stableRoot, sandboxRoot, installationId] = args;
      const input = { installationId, stableSourceRoot: stableRoot, sandboxSourceRoot: sandboxRoot };
      if (command === '--prepare-project') {
        const inspection = await inspectManagedSources({ stableRoot, sandboxRoot });
        assertSourceIdentity(inspection, stableRoot, sandboxRoot);
        const namespace = await prepareProjectNamespace(input, projectNamespaceDependencies);
        emit(write, { state: 'prepared_empty', installation_id: namespace.installation_id, namespace_digest: projectNamespaceDigest(namespace), source_blocker_count: inspection.blockers.length, ready_for_launch: false });
        return { code: 0, namespace, inspection, ready_for_launch: false };
      }
      const namespace = await readProjectNamespace(input, projectNamespaceDependencies);
      const expectedRoot = projectNamespacePath(sandboxRoot, installationId);
      if (path.resolve(namespace.root) !== expectedRoot || namespace.installation_id !== installationId.toLowerCase()) {
        throw Object.assign(new Error('project namespace identity mismatch'), { code: 'NAMESPACE_IDENTITY_MISMATCH' });
      }
      const namespaceDigest = projectNamespaceDigest(namespace);
      if (command === '--check-project') {
        emit(write, { state: namespace.stage, installation_id: namespace.installation_id, mode: namespace.mode, ready_for_launch: false });
        return { code: 0, namespace, ready_for_launch: false };
      }
      const inspection = await inspectManagedSources({ stableRoot, sandboxRoot });
      assertSourceIdentity(inspection, stableRoot, sandboxRoot);
      await revalidateManagedInspection(inspection);
      const freshNamespace = await readProjectNamespace(input, projectNamespaceDependencies);
      if (projectNamespaceDigest(freshNamespace) !== namespaceDigest) {
        throw Object.assign(new Error('project namespace changed during source inspection'), { code: 'NAMESPACE_IDENTITY_MISMATCH' });
      }
      const summary = conciseInspection(inspection);
      const code = summary.blocker_count > 0 ? 3 : 0;
      emit(write, { ...summary, installation_id: freshNamespace.installation_id, namespace_digest: namespaceDigest, code });
      return { code, namespace: freshNamespace, inspection, namespace_digest: namespaceDigest, ...summary };
    }
    if (args.length === 4 && args[0] === '--review-project' && args.slice(1).every((arg) => typeof arg === 'string' && arg.length > 0) && UUID.test(args[3]) && path.isAbsolute(args[1]) && path.isAbsolute(args[2])) {
      const [, stableRoot, sandboxRoot, installationId] = args;
      const input = { installationId, stableSourceRoot: stableRoot, sandboxSourceRoot: sandboxRoot };
      const namespace = await readProjectNamespace(input, projectNamespaceDependencies);
      const expectedRoot = projectNamespacePath(sandboxRoot, installationId);
      if (path.resolve(namespace.root) !== expectedRoot || namespace.installation_id !== installationId.toLowerCase()) throw Object.assign(new Error('project namespace identity mismatch'), { code: 'NAMESPACE_IDENTITY_MISMATCH' });
      const namespaceDigest = projectNamespaceDigest(namespace);
      const review = await inspectManagedReview({ stableRoot, sandboxRoot });
      await revalidateManagedReview(review);
      const freshNamespace = await readProjectNamespace(input, projectNamespaceDependencies);
      if (projectNamespaceDigest(freshNamespace) !== namespaceDigest || path.resolve(freshNamespace.root) !== expectedRoot || freshNamespace.installation_id !== installationId.toLowerCase()) throw Object.assign(new Error('project namespace changed during source review'), { code: 'NAMESPACE_IDENTITY_MISMATCH' });
      const reviewDigest = createHash('sha256').update(`${namespaceDigest}:${review.review_digest}`, 'utf8').digest('hex');
      const code = review.blockers.length > 0 ? 3 : 0;
      const envelope = { state: 'reviewed', installation_id: freshNamespace.installation_id, namespace_digest: namespaceDigest, review_digest: reviewDigest, review, code };
      emit(write, envelope);
      return { code, namespace: freshNamespace, namespace_digest: namespaceDigest, review_digest: reviewDigest, review, ready_for_launch: false };
    }
    if (args.length === 2 && (args[0] === '--check' || args[0] === '--inspect') && typeof args[1] === 'string' && path.isAbsolute(args[1])) {
      const installationRoot = path.resolve(args[1]);
      const namespace = await readManagedNamespace(installationRoot, namespaceDependencies);
      if (path.resolve(namespace.root) !== installationRoot || managedNamespacePath(namespaceDependencies?.localAppData ?? process.env.LOCALAPPDATA, namespace.installation_id) !== installationRoot) {
        throw Object.assign(new Error('namespace identity mismatch'), { code: 'NAMESPACE_IDENTITY_MISMATCH' });
      }
      if (args[0] === '--check') {
        emit(write, { state: namespace.stage, installation_id: namespace.installation_id, mode: namespace.mode, ready_for_launch: false });
        return { code: 0, namespace, ready_for_launch: false };
      }
      const inspection = await inspectManagedSources({ stableRoot: namespace.source_roots.stable, sandboxRoot: namespace.source_roots.sandbox });
      await revalidateManagedInspection(inspection);
      const freshNamespace = await readManagedNamespace(installationRoot, namespaceDependencies);
      if (managedNamespaceDigest(freshNamespace) !== managedNamespaceDigest(namespace)) {
        throw Object.assign(new Error('namespace changed during source inspection'), { code: 'NAMESPACE_IDENTITY_MISMATCH' });
      }
      const summary = conciseInspection(inspection);
      const code = summary.blocker_count > 0 ? 3 : 0;
      emit(write, { ...summary, installation_id: namespace.installation_id, code });
      return { code, namespace: freshNamespace, inspection, ...summary };
    }
    emit(write, { state: 'refused', code: 'INVALID_USAGE', ready_for_launch: false });
    return { code: 2, ready_for_launch: false, error_code: 'INVALID_USAGE' };
  } catch (error) {
    const errorCode = safeErrorCode(error);
    emit(write, { state: 'refused', code: errorCode, ready_for_launch: false });
    return { code: 1, ready_for_launch: false, error_code: errorCode };
  }
}

if (main) {
  const result = await runManagedCli();
  process.exitCode = result.code;
}


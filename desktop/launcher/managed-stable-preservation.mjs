import {createHash} from 'node:crypto';
const HASH=/^[a-f0-9]{64}$/;
const OID=/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/;
const isRecord=value=>value!==null&&typeof value==='object'&&!Array.isArray(value);
export function validateManagedStablePreservation(plan) {
  if (!Object.hasOwn(plan, "stablePreservation")) return;
  const value = plan.stablePreservation;
  const keys = ["mode", "files", "rawBytes", "expectedTree", "expectedCommit"];
  if (!isRecord(value) || Object.keys(value).length !== keys.length || keys.some(key => !Object.hasOwn(value, key))
      || value.mode !== "managed_stable_working_preservation_v1" || !Array.isArray(value.files) || !value.files.length
      || value.files.length > plan.selectedPaths.length || !Number.isSafeInteger(value.rawBytes) || value.rawBytes < 0 || value.rawBytes > 8 * 1024 * 1024
      || !OID.test(value.expectedTree) || !OID.test(value.expectedCommit) || value.expectedTree.length !== plan.stable.head.length || value.expectedCommit.length !== plan.stable.head.length) throw new TypeError("stable preservation binding invalid");
  const seen = new Set(); let total = 0;
  for (const file of value.files) {
    const fileKeys = ["path", "sha256", "oid", "mode", "bytesBase64"];
    if (!isRecord(file) || Object.keys(file).length !== fileKeys.length || fileKeys.some(key => !Object.hasOwn(file, key))
        || typeof file.path !== "string" || seen.has(file.path) || !plan.selectedPaths.includes(file.path)
        || !HASH.test(file.sha256) || !OID.test(file.oid) || file.oid.length !== plan.stable.head.length || !/^(?:100644|100755)$/.test(file.mode)
        || typeof file.bytesBase64 !== "string" || file.bytesBase64.length > Math.ceil(4 * 1024 * 1024 / 3) * 4) throw new TypeError("stable preservation file invalid");
    const bytes = Buffer.from(file.bytesBase64, "base64");
    const before = plan.files.find(entry => entry.path === file.path);
    const oid = createHash(plan.stable.head.length === 64 ? "sha256" : "sha1").update(Buffer.from(`blob ${bytes.length}\0`)).update(bytes).digest("hex");
    if (bytes.length > 4 * 1024 * 1024 || bytes.toString("base64") !== file.bytesBase64
        || createHash("sha256").update(bytes).digest("hex") !== file.sha256 || oid !== file.oid
        || !before || before.action !== "replace" || before.beforeSha256 !== file.sha256 || before.expectedBaseMode !== file.mode) throw new TypeError("stable preservation original bytes invalid");
    seen.add(file.path); total += bytes.length;
  }
  if (total !== value.rawBytes) throw new TypeError("stable preservation byte total invalid");
  const author = "Managed Selected Merge <managed-selected-merge@invalid> 0 +0000";
  const objectId = (tree, parent, message) => {
    const bytes = Buffer.from(`tree ${tree}\nparent ${parent}\nauthor ${author}\ncommitter ${author}\n\n${message}\n`);
    return createHash(plan.stable.head.length === 64 ? "sha256" : "sha1").update(Buffer.from(`commit ${bytes.length}\0`)).update(bytes).digest("hex");
  };
  if (objectId(value.expectedTree, plan.stable.head, `Preserve stable working bytes for checkpoint ${plan.checkpointDigest}`) !== value.expectedCommit
      || objectId(plan.expectedMergedTree, value.expectedCommit, `Selected content from checkpoint ${plan.checkpointDigest}`) !== plan.expectedCommit) throw new TypeError("stable preservation commit ancestry invalid");
}

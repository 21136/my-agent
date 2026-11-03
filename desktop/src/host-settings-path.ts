export async function getOptionalHostPath(getPath?: () => Promise<string>): Promise<string> {
  try {
    return (await getPath?.()) ?? "";
  } catch {
    return "";
  }
}

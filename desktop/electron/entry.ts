const managed = process.argv.includes("--managed-host-v1");

if (managed) {
  void import("./managed-host-entry.js");
} else {
  void import("./main.js");
}

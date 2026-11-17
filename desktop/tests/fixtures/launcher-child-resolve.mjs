import { resolveStockElectronDist } from "../../scripts/windows-branded-electron-lib.mjs";

const desktopDir = process.argv[2];
if (!desktopDir) process.exit(2);

const stock = resolveStockElectronDist(desktopDir);
if (!stock) process.exit(3);

const override = process.env.ELECTRON_OVERRIDE_DIST_PATH;
if (!override || stock.distDir === override) process.exit(4);

console.log(stock.distDir);
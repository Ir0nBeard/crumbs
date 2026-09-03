/**
 * Crumbs attribution SDK — ES module entry.
 *
 * Usage (Node / bundlers):
 *   import { createCrumbs } from "@crumbs/sdk";
 *   const crumbs = createCrumbs({ merchantId: "m_...", apiUrl: "https://api.crumbs.dev" });
 *   await crumbs.setConsent("granted");
 *   const journey = await crumbs.requestJourney();
 *
 * The implementation lives in src/crumbs-core.cjs (UMD factory). Under Node ESM
 * the file is loaded as CommonJS (module.exports path); in browsers the IIFE
 * bundle (dist/crumbs.iife.js) exposes window.Crumbs.createCrumbs.
 */
import CrumbsCore from "./src/crumbs-core.cjs";

export const createCrumbs = CrumbsCore.createCrumbs;
export const SDK_VERSION = "0.1.0";
export default CrumbsCore;

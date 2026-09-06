import { contextBridge } from "electron";

// Exposed API surface for the renderer. Kept minimal for Phase 0 -
// grows as backend-integrated features are added.
contextBridge.exposeInMainWorld("anprAtcc", {
  version: process.env.npm_package_version ?? "0.1.0",
});

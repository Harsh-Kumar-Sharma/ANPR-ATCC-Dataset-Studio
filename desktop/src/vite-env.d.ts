/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** API base URL baked in at build time. Unset for the desktop build,
   *  which keeps http://127.0.0.1:8000; the web image sets it to /api. */
  readonly VITE_API_BASE?: string;
}

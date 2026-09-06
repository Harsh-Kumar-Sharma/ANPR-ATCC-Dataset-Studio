# Desktop

Electron + React + TypeScript desktop shell for the ANPR + ATCC
Dataset Studio. See [`../docs/06_UI_UX_SPEC.md`](../docs/06_UI_UX_SPEC.md).

## Setup

```bash
npm install
```

## Run in development

```bash
npm run dev
```

Starts the Vite dev server and launches Electron pointed at it.

## Type-check and build

```bash
npm run build
```

## Run tests

```bash
npm test
```

## Building an installer

```bash
npm run dist
```

Runs `npm run build` (renderer + Electron main/preload), then
`electron-builder`, which packages everything plus the bundled
backend into an installer under `release/` (Windows: NSIS installer,
per-user install - no admin rights required).

**Build the backend executable first** - see
[`../backend/README.md`](../backend/README.md#building-a-standalone-executable-for-the-desktop-installer).
`electron-builder`'s `build.extraResources` in `package.json` expects
it at `../backend/dist/anpr-atcc-backend/`.

`electron/main.ts` spawns that bundled executable as a child process
when the packaged app starts (not needed in `npm run dev` - that talks
to whatever backend you're running separately, per the root
[`README.md`](../README.md)), and stops it when the app quits.

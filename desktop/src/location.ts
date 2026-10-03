/**
 * Where the user is in the studio, kept in the URL's hash.
 *
 * A reload used to land on the project list whatever was open. The
 * hash survives a reload - in the browser and in Electron alike - and
 * lives in the address bar, so it is also this window's own place:
 * a second window keeps its own.
 *
 *   #/p/<project>/<module>[/task/<id>][/frame/<id>][/track/<id>][/watch/<source>]
 */

export interface Place {
  projectId: string;
  tab: string;
  taskId?: string;
  frameId?: string;
  trackId?: string;
  watchSourceId?: string;
}

const KEYS = { task: "taskId", frame: "frameId", track: "trackId", watch: "watchSourceId" } as const;

export function readPlace(hash: string = window.location.hash): Place | null {
  const parts = hash.replace(/^#\/?/, "").split("/").filter(Boolean).map(decodeURIComponent);
  if (parts[0] !== "p" || !parts[1]) return null;
  const place: Place = { projectId: parts[1], tab: parts[2] ?? "" };
  for (let i = 3; i + 1 < parts.length; i += 2) {
    const key = KEYS[parts[i] as keyof typeof KEYS];
    if (key) place[key] = parts[i + 1];
  }
  return place;
}

export function placeToHash(place: Place | null): string {
  if (!place) return "";
  const parts = ["p", place.projectId, place.tab];
  for (const [segment, key] of Object.entries(KEYS)) {
    const value = place[key];
    if (value) parts.push(segment, value);
  }
  return `#/${parts.map(encodeURIComponent).join("/")}`;
}

/** Record the place without adding a history entry for every click. */
export function writePlace(place: Place | null): void {
  const hash = placeToHash(place);
  if (hash === window.location.hash || (!hash && !window.location.hash)) return;
  const url = `${window.location.pathname}${window.location.search}${hash}`;
  window.history.replaceState(window.history.state, "", url);
}

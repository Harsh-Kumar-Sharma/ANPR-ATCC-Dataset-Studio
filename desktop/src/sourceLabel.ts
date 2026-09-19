import type { Source } from "./types";

/**
 * What to call a source on screen.
 *
 * The filename for a video. For a live stream the last path segment is
 * the channel number - two cameras in a real project both displayed as
 * "1" - so it is named by host and channel instead. Credentials in the
 * URL are left out: an RTSP url carries the password in plain sight and
 * there is no reason to put it on screen.
 *
 * Shared because the Sources panel and the label queue's source picker
 * have to agree. Two copies would drift, and the user would be looking
 * at two names for one clip.
 */
export function sourceLabel(source: { type: string; path_or_uri: string }): string {
  if (source.type !== "rtsp") return source.path_or_uri.split(/[\\/]/).pop() ?? source.path_or_uri;
  try {
    const url = new URL(source.path_or_uri);
    const channel = url.pathname.split("/").filter(Boolean).pop();
    return channel ? `${url.hostname} · ch ${channel}` : url.hostname;
  } catch {
    return source.path_or_uri;
  }
}

export type LabelledSource = Pick<Source, "type" | "path_or_uri">;

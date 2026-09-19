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
  const host = rtspHost(source.path_or_uri);
  const channel = rtspChannel(source.path_or_uri);
  if (host && channel) return `${host} · ch ${channel}`;
  return host || source.path_or_uri;
}

/**
 * The host of an RTSP url, without the credentials.
 *
 * Read off the string rather than through `URL`: `rtsp:` is not a
 * scheme the URL parser treats as special, and it was observed
 * returning an empty hostname for a url this parses correctly - which
 * left a camera displayed as "· ch 1", named after nothing. Falling
 * back to the raw url would have been better than that, but reading
 * the authority directly is better still, and it is four lines.
 */
function rtspHost(uri: string): string {
  const authority = afterScheme(uri).split(/[/?#]/)[0] ?? "";
  // Everything before the last "@" is user:password and does not
  // belong on screen: an RTSP url carries the password in plain sight.
  const hostAndPort = authority.slice(authority.lastIndexOf("@") + 1);
  // The port is noise next to the channel. An IPv6 literal is full of
  // colons, so its port is whatever follows the closing bracket.
  if (hostAndPort.startsWith("[")) {
    const close = hostAndPort.indexOf("]");
    return close === -1 ? hostAndPort : hostAndPort.slice(0, close + 1);
  }
  return hostAndPort.split(":")[0] ?? "";
}

/** The last path segment, which on these cameras is the channel. */
function rtspChannel(uri: string): string {
  const rest = afterScheme(uri);
  const path = rest.slice(rest.search(/[/?#]/) === -1 ? rest.length : rest.search(/[/?#]/));
  return (path.split(/[?#]/)[0] ?? "").split("/").filter(Boolean).pop() ?? "";
}

/** Everything after `scheme://`, or "" when there is no such prefix -
 *  a string that is not a url has no host and no channel to find. */
function afterScheme(uri: string): string {
  const match = /^[a-z][a-z0-9+.-]*:\/\/(.*)$/is.exec(uri);
  return match ? match[1] : "";
}

export type LabelledSource = Pick<Source, "type" | "path_or_uri">;

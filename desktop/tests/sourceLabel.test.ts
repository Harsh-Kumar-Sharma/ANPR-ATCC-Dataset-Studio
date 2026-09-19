/**
 * What a source is called on screen.
 *
 * Two cameras in a real project both displayed as "1", because the
 * last path segment is the channel number. Naming them by host fixed
 * that - and then one of them displayed as "· ch 1", named after
 * nothing, because the URL parser handed back an empty hostname for a
 * url it had no trouble with elsewhere.
 */
import { describe, expect, it } from "vitest";
import { sourceLabel } from "../src/sourceLabel";

const rtsp = (uri: string) => sourceLabel({ type: "rtsp", path_or_uri: uri });

describe("sourceLabel", () => {
  it("calls a video by its filename", () => {
    expect(sourceLabel({ type: "video", path_or_uri: "C:\\clips\\day_anpr_gantry.mp4" })).toBe(
      "day_anpr_gantry.mp4",
    );
    expect(sourceLabel({ type: "video", path_or_uri: "/mnt/clips/night.mp4" })).toBe("night.mp4");
  });

  it("names a camera by host and channel", () => {
    expect(rtsp("rtsp://10.0.0.4:9001/Streaming/channels/1")).toBe("10.0.0.4 · ch 1");
  });

  it("tells two channels of one camera apart", () => {
    // The complaint this answers: both displayed as "1".
    expect(rtsp("rtsp://10.0.0.4:9001/Streaming/channels/1")).not.toBe(
      rtsp("rtsp://10.0.0.4:9001/Streaming/channels/2"),
    );
  });

  it("keeps the password off the screen", () => {
    const label = rtsp("rtsp://admin:Admin1234@160.187.179.196:9001/Streaming/channels/1");

    expect(label).toBe("160.187.179.196 · ch 1");
    expect(label).not.toContain("Admin1234");
    expect(label).not.toContain("admin");
  });

  it("survives a password containing an at sign", () => {
    expect(rtsp("rtsp://admin:p@ss@10.0.0.4/Streaming/channels/2")).toBe("10.0.0.4 · ch 2");
  });

  it("keeps an IPv6 address in one piece", () => {
    expect(rtsp("rtsp://[2001:db8::1]:554/Streaming/channels/1")).toBe("[2001:db8::1] · ch 1");
  });

  it("names a camera with no channel path after its host", () => {
    expect(rtsp("rtsp://10.0.0.4:554")).toBe("10.0.0.4");
  });

  it("falls back to the url rather than to nothing", () => {
    // What went wrong on screen: a camera called "· ch 1".
    expect(rtsp("not a url at all")).toBe("not a url at all");
    expect(rtsp("")).toBe("");
  });

  it("never renders a label that starts with the separator", () => {
    for (const uri of ["rtsp://", "rtsp:///Streaming/channels/1", "://x/1", "rtsp://@/1"]) {
      expect(rtsp(uri).startsWith("·")).toBe(false);
    }
  });
});

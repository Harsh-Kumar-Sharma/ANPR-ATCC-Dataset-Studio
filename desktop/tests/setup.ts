import "@testing-library/jest-dom/vitest";

// jsdom has no layout, so it implements no scrolling. Components that
// bring something into view are doing a real thing in a real browser;
// here it is a no-op that would otherwise throw.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}

// No test talks to a real backend. Calls a test forgot to mock used to
// fall through to whatever was listening on :8000 - harmless while the
// API was open, but now that it answers 401 a stray call signs the app
// out in the middle of a test. A test that wants fetch mocks it itself.
globalThis.fetch = (() => Promise.reject(new TypeError("Network is disabled in tests"))) as typeof fetch;

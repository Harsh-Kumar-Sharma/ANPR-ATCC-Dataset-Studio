import "@testing-library/jest-dom/vitest";

// jsdom has no layout, so it implements no scrolling. Components that
// bring something into view are doing a real thing in a real browser;
// here it is a no-op that would otherwise throw.
if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = function scrollIntoView() {};
}

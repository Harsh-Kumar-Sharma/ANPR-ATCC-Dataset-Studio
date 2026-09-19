# 04: Frames belong to a source, and the app knows it

**What to build:** Everywhere frames are listed, they can be narrowed
to one source - and the Label tab starts by asking which source you are
working on.

Today the queue mixes every source's frames into one list ordered by
source then frame index, so with three sources you cannot tell whose
frames you are looking at or work one clip at a time. You said this
directly: you do not know which frames belong to which source.

**Blocked by:** nothing

**Status:** done

- [x] `GET /projects/{id}/frames` takes a `source_id` filter, and the progress counts respect it
- [x] The Label tab has a source picker; choosing one narrows the queue and the progress line to that source
- [x] The picker shows each source's own numbers: how many frames, how many labelled, how many left
- [x] The choice is remembered per project, the way the last open frame already is
- [x] An RTSP source is named by its host and channel rather than the last path segment - both of yours currently display as "1"
- [x] Clicking a source in the Sources panel opens the Label tab already narrowed to it

**Notes:**

- The list and the counts are filtered in one place (`_openable_frames`)
  so they cannot drift: a filtered list beside an unfiltered count is
  the same bug as a stale count, with a different cause.
- An unknown or other-project `source_id` is a 404, not an empty list.
  An empty list reads identically to "this source has nothing to
  label", which is the wrong thing to tell someone with a stale id.
- `GET /frames/by-source` is one call, not one per source, and is
  ordered with the most work left first - a labeller opening the tab
  wants the clip that still needs doing.
- A source with no frames yet still appears in the picker, otherwise
  there is no way to see that it has nothing to label.
- The queue fetches the per-source list *first* and alone: a remembered
  source may have been deleted since (ticket 01 made that easy), and
  asking for its frames would 404 and strand the tab on an error it
  cannot be clicked out of. It falls back to all sources and forgets
  the dead id.
- `sourceLabel` moved to `desktop/src/sourceLabel.ts` so the Sources
  panel and the picker cannot drift into two names for one clip.
- The picker is hidden when there is one source: that is not a choice.

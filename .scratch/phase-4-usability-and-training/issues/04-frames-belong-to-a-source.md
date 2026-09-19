# 04: Frames belong to a source, and the app knows it

**What to build:** Everywhere frames are listed, they can be narrowed
to one source - and the Label tab starts by asking which source you are
working on.

Today the queue mixes every source's frames into one list ordered by
source then frame index, so with three sources you cannot tell whose
frames you are looking at or work one clip at a time. You said this
directly: you do not know which frames belong to which source.

**Blocked by:** nothing

**Status:** ready-for-agent

- [ ] `GET /projects/{id}/frames` takes a `source_id` filter, and the progress counts respect it
- [ ] The Label tab has a source picker; choosing one narrows the queue and the progress line to that source
- [ ] The picker shows each source's own numbers: how many frames, how many labelled, how many left
- [ ] The choice is remembered per project, the way the last open frame already is
- [ ] An RTSP source is named by its host and channel rather than the last path segment - both of yours currently display as "1"
- [ ] Clicking a source in the Sources panel opens the Label tab already narrowed to it

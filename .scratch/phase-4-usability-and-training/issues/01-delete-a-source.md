# 01: Delete a source and everything it holds

**What to build:** A source can be removed from the Sources panel,
taking its frames, runs, tracks, detections, labels and files with it.

There is no way to do this today. Your ANPR project holds two dead RTSP
sources - both showing as "1", 0x0, no usable frames - and nothing in
the app can clear them.

**Blocked by:** nothing

**Status:** ready-for-agent

The project delete built in the last session is the pattern: count what
would go, name it to confirm, refuse while work is running, and never
recursively delete a directory that is not one this app made. A source
is smaller but the same shape, and it should reuse the same service
helpers rather than growing a second cascade.

- [ ] `DELETE /projects/{id}/sources/{id}` removes the source, its processing runs, tracks, frame candidates, OCR readings, frames and annotations
- [ ] `GET /projects/{id}/sources/{id}/contents` says what would go, including the size of the files on disk
- [ ] Deleting is refused while a job or a live capture is running against that source
- [ ] The source's own files go: its copy of the video, its track crops, its materialised frames. Dataset exports do not, because a version already exported is immutable and shared with other sources
- [ ] Another source in the same project is untouched, and the project itself survives
- [ ] The Sources panel offers it per row, says what will be destroyed, and requires confirmation

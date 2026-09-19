# 01: Delete a source and everything it holds

**What to build:** A source can be removed from the Sources panel,
taking its frames, runs, tracks, detections, labels and files with it.

There is no way to do this today. Your ANPR project holds two dead RTSP
sources - both showing as "1", 0x0, no usable frames - and nothing in
the app can clear them.

**Blocked by:** nothing

**Status:** done

The project delete built in the last session is the pattern: count what
would go, name it to confirm, refuse while work is running, and never
recursively delete a directory that is not one this app made. A source
is smaller but the same shape, and it should reuse the same service
helpers rather than growing a second cascade.

- [x] `DELETE /projects/{id}/sources/{id}` removes the source, its processing runs, tracks, frame candidates, OCR readings, frames and annotations
- [x] `GET /projects/{id}/sources/{id}/contents` says what would go, including the size of the files on disk
- [x] Deleting is refused while a job or a live capture is running against that source
- [x] The source's own files go: its copy of the video, its track crops, its materialised frames. Dataset exports do not, because a version already exported is immutable and shared with other sources
- [x] Another source in the same project is untouched, and the project itself survives
- [x] The Sources panel offers it per row, says what will be destroyed, and requires confirmation

**Files are removed by name, not by clearing a directory.** A source
owns its copy of the video, the crops of its own tracks and the frames
decoded from it, each at a known path; the directories around them
belong to every source in the project. Each path is checked to be
inside this project's workspace before anything is deleted, so a
`path_or_uri` still pointing at the user's own file somewhere else is
left alone - importing copies the video in, but an older row may not.

**No typed confirmation here, unlike a project.** A project is the
whole of someone's work and is reachable from one screen; a source is
one clip among several, removed far more often, and its confirmation
already names what it holds. The interlocks that matter - refusing
while work is running, and never deleting outside the workspace - are
the same.

**The cascade helpers moved to `services/cascade.py`.** Project and
source deletion are the same shape at two scales, and the chunked id
gathering exists because one bound parameter per frame runs into
SQLite's cap. Two copies of that would have drifted.

**Two things found by running it against the real project.** Both RTSP
sources displayed as "1", because that is the channel number at the
end of the URL - they are named by host and channel now, with the
credentials in the URL kept off the screen. And the panel offered
"Detect + Track" on a live stream, which has no file to decode and no
frame count, so the job could only fail. It had: both of those sources
carry a failed job reading "frame_count must be positive". That button
is now offered for video files only.

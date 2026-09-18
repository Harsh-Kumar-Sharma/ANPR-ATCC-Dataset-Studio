# 02: Run detection as a background job with live progress

**What to build:** Starting detection on a source returns immediately and the user watches it progress in a jobs panel, instead of an HTTP request blocking for one to three minutes with no progress and no way out.

This is the tracer bullet through the whole job machinery. Training needs the same runner later and will take hours, so the machinery is built once here and proven on the shorter, more forgiving case.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

- [ ] A job record carries type, status, progress, process id, timestamps and error text
- [ ] The runner launches work as a detached subprocess and reports progress back to the app
- [ ] A jobs API can list jobs, fetch one, and stream its progress
- [ ] The source-processing endpoint submits a job and returns straight away rather than running detection inline
- [ ] A jobs panel lists jobs, and a persistent indicator shows a run in flight from anywhere in the app
- [ ] A job that fails surfaces its error to the user instead of disappearing
- [ ] Only one training job may run at a time once training exists; detection jobs are not subject to that limit, and the runner is designed so this rule has somewhere to live

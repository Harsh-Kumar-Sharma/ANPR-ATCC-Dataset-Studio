# 02: Run detection as a background job with live progress

**What to build:** Starting detection on a source returns immediately and the user watches it progress in a jobs panel, instead of an HTTP request blocking for one to three minutes with no progress and no way out.

This is the tracer bullet through the whole job machinery. Training needs the same runner later and will take hours, so the machinery is built once here and proven on the shorter, more forgiving case.

**Blocked by:** None (can start immediately)

**Status:** done

- [x] A job record carries type, status, progress, process id, timestamps and error text
- [x] The runner launches work as a detached subprocess and reports progress back to the app
- [x] A jobs API can list jobs, fetch one, and stream its progress
- [x] The source-processing endpoint submits a job and returns straight away rather than running detection inline
- [x] A jobs panel lists jobs, and a persistent indicator shows a run in flight from anywhere in the app
- [x] A job that fails surfaces its error to the user instead of disappearing
- [ ] Only one training job may run at a time once training exists; detection jobs are not subject to that limit, and the runner is designed so this rule has somewhere to live

**Note on the concurrency criterion:** the one-training-job-at-a-time rule is
not enforced yet, because there is no training job type to enforce it against.
`submit_job` is the single chokepoint where it will go. Detection stays
unrestricted, as specified; double-submitting the *same source* is already
rejected, and that guard now keys off `pending` as well as `running` - a
queued-but-not-started run would otherwise have slipped straight past it.

# 03: Cancel a running job

**What to build:** A user who started the wrong run, or picked the wrong source, can stop it from the jobs panel and get the machine back.

**Blocked by:** 02 (Run detection as a background job with live progress)

**Status:** done

- [x] Cancelling from the jobs panel terminates the subprocess rather than orphaning it
- [x] The job and its processing run both land in a clean terminal state that reads as cancelled, not failed and not running
- [x] Partial output from a cancelled run is never presented as a finished run
- [x] Cancelling an already-finished job is harmless

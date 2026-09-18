# 04: Jobs survive closing the app

**What to build:** A user can close the app mid-run, reopen it, and find the job still going with live progress. This is the behaviour training will depend on — a multi-hour training run must not be hostage to the desktop window staying open.

**Blocked by:** 02 (Run detection as a background job with live progress)

**Status:** ready-for-agent

- [ ] Closing the app does not kill a running job's subprocess
- [ ] On startup the app reattaches to still-running jobs and resumes showing their progress
- [ ] A job whose process died while the app was closed is reconciled to a failed state on startup, not left running forever
- [ ] Cancel still works on a reattached job

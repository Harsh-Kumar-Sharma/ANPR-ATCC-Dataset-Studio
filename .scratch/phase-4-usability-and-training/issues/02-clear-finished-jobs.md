# 02: Clear finished jobs out of the list

**What to build:** A cancelled, failed or finished job can be removed
from the Jobs panel, individually or all at once.

Your database holds five dead jobs - two cancelled detections, three
failed, one cancelled selection - and they will sit in that list
forever. The one that failed did so because of the progress-file bug
fixed earlier today; it is not coming back to life.

**Blocked by:** nothing

**Status:** done

- [x] `DELETE /jobs/{id}` removes a job that has finished, and refuses one that is still pending or running with a clear reason
- [x] `POST /jobs/clear-finished` removes every finished job for a project in one go, and says how many it removed
- [x] The job's progress file and worker log go with it - those live outside the workspace and nothing has ever cleaned them up
- [x] The Jobs panel offers "Dismiss" per finished job and "Clear finished" for the list
- [x] A running job cannot be dismissed, and the panel does not offer it

**Two files per job that nothing had ever cleaned up.** A job's
progress file and its worker log live in the jobs directory, outside
any project workspace, so neither a project delete nor a source delete
ever reached them. Every job that has ever run has been leaving both
behind. Dismissing takes them with it.

**A running job cannot be dismissed, and the message says why.** Its
worker is still writing to that row and those files, and cancel is the
one path that actually stops the process rather than forgetting about
it. The panel does not offer Dismiss until the job is finished, and the
server refuses it anyway - the two can disagree for as long as it takes
a poll to land.

**`/jobs/clear-finished` is declared before `/jobs/{job_id}`**, or the
parameter route swallows the literal path and "clear-finished" becomes
a job id.

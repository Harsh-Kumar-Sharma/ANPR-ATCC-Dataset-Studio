# 02: Clear finished jobs out of the list

**What to build:** A cancelled, failed or finished job can be removed
from the Jobs panel, individually or all at once.

Your database holds five dead jobs - two cancelled detections, three
failed, one cancelled selection - and they will sit in that list
forever. The one that failed did so because of the progress-file bug
fixed earlier today; it is not coming back to life.

**Blocked by:** nothing

**Status:** ready-for-agent

- [ ] `DELETE /jobs/{id}` removes a job that has finished, and refuses one that is still pending or running with a clear reason
- [ ] `POST /jobs/clear-finished` removes every finished job for a project in one go, and says how many it removed
- [ ] The job's progress file and worker log go with it - those live outside the workspace and nothing has ever cleaned them up
- [ ] The Jobs panel offers "Dismiss" per finished job and "Clear finished" for the list
- [ ] A running job cannot be dismissed, and the panel does not offer it

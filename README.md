## Immediate TODO

- [x] jobs are still being added to blacklisted gpus
- [x] fix screen attaching not showing anything
- [x] merge stdout and stderr
- [x] remove spaces from command combinations
- [x] freeze git states when adding jobs
- [x] if -r in command it bugs out thinking its repeat
- [x] runtime doesnt update if gpu is blacklisted
- [x] Classify failed jobs
- [x] automatically detect wandb runs
- [x] Webhooks for starting, completed, and failed jobs (for discord)
- [x] make webhooks prettier
- [x] if a job doesnt have wandb after 5 minutes, stop pinging it
- [x] clean up git tags that are unused
- [x] make sure the job started webhook waits
- [x] multi user support
- [x] cli: prompt yes or no before removing, killing, or adding
- [x] cli: put wandb url in nexus status at cli
- [x] cli: put runtime and time started on same line
- [x] cli: filter history with command regex
- [x] cli: in history put failed and completed together, then I shuold be able to see which  and which completed
- [ ] cli: nx should also work
- [ ] sometimes a job doesnt die, should probably SIGKILL
- [ ] Look for wandb in longer intervals in the scheduler, then if not found after a while update original message
- [ ] easy way to show logs for running jobs on discord
- [ ] add tests
- [ ] its starting jobs on non available gpus
- [ ] actually get gpu processes
- [ ] cli: follow logs with -f
- [ ] figure out why git tag removal fails some times
- [ ] installation walkthrough with the service
- [ ] figure out why job failed webhook failes sometimes
- [ ] cli: More job details when printing job stuff, especially when removing or killing
- [ ] Automatically check for updates
- [ ] multi gpu jobs
- [ ] dependent jobs (a after b is done)
- [ ] cli: ensure cli and api version align
- [ ] refactor, move more things away from cli and to the api
- [ ] sqlite for state management

## Longterm TODO

- [ ] systemd service
- [ ] multi node
- [ ] priority jobs
- [ ] vram / flop minimum
- [ ] rust rewrite for static binaries that don't require python (or venvs to be activated)
- [ ] pretty TUI front end

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
- [ ] multi user support
- [ ] Automatically check for updates
- [ ] a way to quickly find the logs of recent crashes
- [ ] filter history with command regex
- [ ] clean up git tags that are unused
- [ ] make sure the job started webhook waits
- [ ] put wandb url in nexus status at cli
- [ ] multi gpu jobs
- [ ] dependent jobs (a after b is done)
- [ ] ensure cli and api version align, else restart
- [ ] refactor, move more things away from cli and to the api
- [ ] prompt yes or no before removing, killing, or adding
- [ ] put runtime and time started on same line
- [ ] sqlite for state management
- [ ] multiline bash
- [ ] priority jobs

## Longterm TODO

- [ ] rust rewrite for static binaries that don't require python (or venvs to be activated)
- [ ] pretty TUI front end
- [ ] multi node
- [ ] priority jobs
- [ ] vram / flop minimum

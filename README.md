## Immediate TODO

- [x] jobs are still being added to blacklisted gpus
- [ ] fix screen attaching not showing anything
- [ ] freeze git states when adding jobs
- [ ] Classify failed jobs
- [ ] Automatically check for updates
- [ ] a way to quickly find the logs of recent crashes
- [ ] multi user support
- [ ] filter history with command regex
- [ ] runtime doesnt update if gpu is blacklisted
- [ ] Webhooks for starting, completed, and failed jobs (for discord)
- [ ] multi gpu jobs
- [ ] share wandb run id
- [ ] automatically detect wandb runs
- [ ] dependent jobs (a after b is done)
- [ ] ensure cli and api version align, else restart
- [ ] refactor, move more things away from cli and to the api
- [ ] prompt yes or now before removing, killing, or adding
- [ ] put runtime and time started on same line
- [x] merge stdout and stderr
- [ ] sqlite for state management
- [ ] if -r in command it bugs out thinking its repeat
- [ ] multiline bash
- [ ] remove spaces from command combinations
- [ ] priority jobs

## Longterm TODO

- [ ] rust rewrite for static binaries that don't require python (or venvs to be activated)
- [ ] pretty TUI front end
- [ ] multi node
- [ ] priority jobs
- [ ] vram / flop minimum

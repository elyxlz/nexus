## Immediate TODO

- [ ] Press Enter for yes, but how to say no in setup?
- [ ] remove log level from config
- [ ] filter by users or not
- [ ] put nexus job id in tmpdir
- [ ] history fked on hermes?
- [ ] save config immedietly as i make a change in nx setup
- [ ] show num of gpus in queue
- [ ] cleanup git functions
- [ ] cleanup git tags optional
- [ ] dependent jobs (a after b is done)
- [ ] nicer error messages on the cli instead of showing apierror
- [ ] better secrets management
- [ ] multi node and auth with dht and dqlite
- [ ] cpu only jobs
- [ ] documentation
- [ ] job execution isolation
- [ ] resources available per job
- [ ] make a new linux user per nexus user
- [ ] when job fails to start get better error message
- [ ] cant push git tag when repo isnt mine
- [ ] warn user when health is low
- [ ] degraded -> under load
- [ ] memory use in health
- [ ] fix wandb searching
- [ ] specify ranodm integer with {RANDINT}
- [ ] lags a bit when killing a job and stufff
- [ ] dont automatically put priority on multi gpu jobs
- [ ] weired bug when i nx remove, repeats jobs:
    The following jobs will be removed from queue:
      • Job 7fnz1k | Command: NAME='canvas-lora-v1' uv run torchrun --nproc_per_... | Queued: 2025-04-14 11:58:24 | User: kale
      • Job 5yzzry | Command: uv run main.py apollo/splitter_rockets/brahms | Queued: 2025-04-14 11:55:43 | User: elyx
      • Job 7fnz1k | Command: NAME='canvas-lora-v1' uv run torchrun --nproc_per_... | Queued: 2025-04-14 11:58:24 | User: kale
      • Job 5yzzry | Command: uv run main.py apollo/splitter_rockets/brahms | Queued: 2025-04-14 11:55:43 | User: elyx
      • Job 7fnz1k | Command: NAME='canvas-lora-v1' uv run torchrun --nproc_per_... | Queued: 2025-04-14 11:58:24 | User: kale
      • Job 5yzzry | Command: uv run main.py apollo/splitter_rockets/brahms | Queued: 2025-04-14 11:55:43 | User: elyx
      • Job 5yzzry | Command: uv run main.py apollo/splitter_rockets/brahms | Queued: 2025-04-14 11:55:43 | User: elyx

- [ ] command autocomplete
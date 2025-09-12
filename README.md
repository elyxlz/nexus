## 🚧 Nexus TODO

change statu view to show queued and running jobs not per gpu stuff
-- force to skip git commit checks
fix bug where jobs get stuck and need to restart nexus

### 🟢 Easy

- [ ] Command autocomplete

### 🟡 Medium

- [ ] Filter job history/queue by user
- [ ] History list broken on Hermes (possibly time-related)
- [ ] Git: clean up helper functions
- [ ] Bug: `nx remove` repeats jobs multiple times
- [ ] Support CPU-only jobs
- [ ] Track per-job resource allocation in metadata
- [ ] Documentation

### 🔴 Hard

- [ ] Dependent jobs (run job B after job A completes)
- [ ] Better secrets management (e.g., encrypted `.env`)
- [ ] Multi-node support (DHT + RqLite for coordination/auth)
- [ ] Full job execution isolation (like slurm does it)
- [ ] Create a dedicated Linux user per Nexus user

## Git Tags for Jobs (optional)

- Enable per-job git tagging by setting in `~/.nexus/config.toml`:
  - `enable_git_tag_push = true`
- When enabled:
  - The CLI creates and pushes a tag `nexus-<job_id>` to `origin` for each submitted job.
  - Jobs get `NEXUS_GIT_TAG` in their environment with the same value.
  - You can detect the tag in scripts via `$NEXUS_GIT_TAG`.

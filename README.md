## 🚧 Nexus TODO

### 🟢 Easy

- [ ] Improve setup messaging: clarify how to say **no** when pressing Enter implies yes
- [ ] Remove `log_level` from config
- [ ] Save config immediately during `nx setup`
- [ ] Show number of GPUs requested in `nx queue`
- [ ] Put Nexus job ID in `tmpdir`
- [ ] Warn user when health is low
- [ ] Rename "degraded" → "under load"
- [ ] Display memory usage in `nx health`
- [ ] Fix `wandb` search fallback
- [ ] Support `{RANDINT}` syntax in commands
- [ ] Avoid auto-priority on multi-GPU jobs
- [ ] Improve startup failure error messages
- [ ] Better CLI error messages (avoid raw APIError)
- [ ] Command autocomplete

### 🟡 Medium

- [ ] Filter job history/queue by user
- [ ] History list broken on Hermes (possibly time-related)
- [ ] Git: clean up helper functions
- [ ] Git: make tagging optional
- [ ] Gracefully skip pushing tag if repo isn't owned
- [ ] Bug: `nx remove` repeats jobs multiple times
- [ ] Kill job responsiveness: reduce lag when killing/refreshing
- [ ] Support CPU-only jobs
- [ ] Track per-job resource allocation in metadata
- [ ] Documentation

### 🔴 Hard

- [ ] Dependent jobs (run job B after job A completes)
- [ ] Better secrets management (e.g., encrypted `.env`)
- [ ] Multi-node support (DHT + DQLite for coordination/auth)
- [ ] Full job execution isolation (process/network/filesystem)
- [ ] Create a dedicated Linux user per Nexus user


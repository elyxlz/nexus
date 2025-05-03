## 🚧 Nexus TODO

### 🟢 Easy

- [ ] Display memory usage in `nx health`
- [ ] Support `{RANDINT}` syntax in commands
- [ ] Command autocomplete
- [ ] Fix `wandb` search fallback

### 🟡 Medium

- [ ] Filter job history/queue by user
- [ ] History list broken on Hermes (possibly time-related)
- [ ] Git: clean up helper functions
- [ ] Git: make tagging optional
- [ ] Gracefully skip pushing tag if repo isn't owned
- [ ] Bug: `nx remove` repeats jobs multiple times
- [ ] Support CPU-only jobs
- [ ] Track per-job resource allocation in metadata
- [ ] Documentation

### 🔴 Hard

- [ ] Dependent jobs (run job B after job A completes)
- [ ] Better secrets management (e.g., encrypted `.env`)
- [ ] Multi-node support (DHT + DQLite for coordination/auth)
- [ ] Full job execution isolation (process/network/filesystem)
- [ ] Create a dedicated Linux user per Nexus user

## 🚧 Nexus TODO

### 🟢 Easy

- [ ] Command autocomplete
- [ ] Fix `wandb` search fallback

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

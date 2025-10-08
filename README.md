## 🚧 Nexus TODO

change statu view to show queued and running jobs not per gpu stuff
fix bug where jobs get stuck and need to restart nexus

### 🟢 Easy

- [ ] Command autocomplete

### 🟡 Medium

- [ ] Filter job history/queue by user
- [ ] Git: clean up helper functions
- [ ] Support CPU-only jobs
- [ ] Track per-job resource allocation in metadata
- [ ] Documentation

### 🔴 Hard

- [ ] Dependent jobs (run job B after job A completes)
- [ ] Better secrets management (e.g., encrypted `.env`)
- [ ] Multi-node support (DHT + RqLite for coordination/auth)
- [ ] Full job execution isolation (like slurm does it)
- [ ] Create a dedicated Linux user per Nexus user

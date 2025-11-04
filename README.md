## 🚧 Nexus TODO

change statu view to show queued and running jobs not per gpu stuff

- [ ] load dotenv
- [ ] broken history
- [ ] if job immedietly completes / fails and never attaches, show logs
- [ ] Filter job history/queue by user
- [ ] Git: clean up helper functions
- [ ] refactor
- [ ] Track per-job resource allocation in metadata
- [ ] Dependent jobs (run job B after job A completes)
- [ ] Better secrets management (e.g., encrypted `.env`)
- [ ] Multi-node support (DHT + RqLite for coordination/auth)
- [ ] Full job execution isolation (like slurm does it)
- [ ] Create a dedicated Linux user per Nexus user
- [ ] Documentation

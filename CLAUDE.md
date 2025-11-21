# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## CRITICAL INSTRUCTIONS - READ FIRST

- ⚠️ **ABSOLUTE REQUIREMENT**: Thoroughly review ALL guidelines in this document BEFORE modifying code
- 🚫 **ZERO COMMENTS POLICY**: DO NOT add comments to code - the code must be self-documenting
- ⛔ **FORBIDDEN**: Do not add explanatory, descriptive, or purpose comments to code under ANY circumstances
- 🔴 **MANDATORY**: Apply ALL style guidelines from this document to your work without exception
- ⚠️ **ZERO TOLERANCE**: The user will not accept violations of these guidelines
- ❌ **REPEATED MISTAKES**: Will result in degraded user trust and experience

## Project Overview

Nexus is a GPU job management system with a client-server architecture. It schedules and runs jobs on GPUs, manages job queues, and provides monitoring capabilities.

### Architecture

**Client-Server Model:**
- **CLI Client** (`nexus.cli`): Command-line interface for users to submit jobs, view status, attach to sessions
- **FastAPI Server** (`nexus.server`): Backend service that manages jobs, schedules work, and monitors system health
- **Communication**: Client talks to server via REST API (HTTP/HTTPS) on configurable host:port
- **Multi-Server Support**: CLI can target multiple servers (local/remote) with distinct configurations per target

**Server Components:**
- **Core** (`nexus.server.core`): Job management, database operations, configuration, context
  - `schemas.py`: Frozen dataclass definitions for Job, statuses, and types
  - `job.py`: Pure functions for job lifecycle (create, start, end, cleanup, kill)
  - `db.py`: SQLite operations for jobs, GPU blacklist, and code artifacts
  - `context.py`: Server context that bundles config and database connection
  - `config.py`: Configuration dataclass with environment variable loading
  - `exceptions.py`: Domain-specific exceptions and exception handling decorators
- **API** (`nexus.server.api`): FastAPI routes and request/response models
  - `router.py`: All HTTP endpoints for jobs, GPUs, health, artifacts
  - `scheduler.py`: Background scheduler loop that starts queued jobs and monitors running jobs
  - `app.py`: FastAPI application with middleware, exception handlers, and lifespan management
  - `models.py`: Pydantic request/response models for API endpoints
- **External** (`nexus.server.external`): Integrations with external systems
  - `gpu.py`: GPU detection via nvidia-smi, availability checking, mock GPU support
  - `wandb_finder.py`: Automatic W&B run URL detection for jobs
  - `notifications.py`: Job status notifications (Discord, phone via Twilio)
  - `system.py`: System health monitoring (CPU, memory, disk, network)
  - `nullpointer.py`: Log upload to 0x0.st for sharing

**Job Execution:**
- Jobs are packaged as tar archives (code artifacts) stored in SQLite
- Jobs run in GNU Screen sessions with unique session names (`nexus_job_{id}`)
- Output captured to `output.log` and `error.log` in job directory
- Exit codes extracted from logs via pattern matching (`COMMAND_EXIT_CODE=N`)
- Job repos cleaned up after completion to save disk space (logs preserved)
- Environment variables: System env + `CUDA_VISIBLE_DEVICES` + job-specific env
- Script generation: Nested bash scripts (outer run.sh, inner job_commands.sh)

**State Management:**
- All state lives in SQLite database (`jobs.db`)
- Jobs flow through states: queued → running → completed/failed/killed
- Scheduler polls database every N seconds (configurable refresh_rate, default 3s)
- GPU allocation tracked via `gpu_idxs` field on Job dataclass
- State changes via `dataclasses.replace()` creating new immutable instances

**Authentication:**
- Token-based auth with Bearer tokens (auto-generated on server start)
- **Localhost bypass**: Connections from 127.0.0.1 or ::1 skip token validation
- **Remote connections**: Require valid API token in Authorization header
- SSH key registration for remote screen session attachment

**Scheduler Behavior:**
The scheduler runs four concurrent async tasks every `refresh_rate` seconds:
1. **`update_running_jobs`**: Detects job completion, extracts exit codes, sends notifications, cleans up repos
2. **`start_queued_jobs`**: Allocates GPUs and launches first queued job if resources available
3. **`update_wandb_urls`**: Searches for W&B metadata and updates Discord messages with run URLs
4. **`check_system_health`**: Logs warnings for unhealthy system state (disk/network/CPU issues)

## Development Commands

### Package Management
- **Install dependencies**: `uv sync`
- **Add package**: `uv add package-name`
- **Add dev package**: `uv add --dev package-name`

### Running the Application
- **Start server**: `uv run nexus-server` or `nexus-server` if installed
- **CLI commands**: `uv run nx <command>` or `nx <command>` if installed
- **First-time setup**: `nx setup`

### Testing
- **Run all tests**: `uv run pytest`
- **Run specific test file**: `uv run pytest tests/server/test_api.py`
- **Run specific test**: `uv run pytest tests/server/test_api.py::test_function_name`
- **Run with coverage**: `uv run pytest --cov=nexus`

### Type Checking
- **Check types**: `uv run pyright` (run from `src/` directory or project root)
- **Project uses strict type checking**: All code must pass pyright without errors

### Building
- **Build package**: `uv build`
- **Install locally**: `uv pip install -e .`

### Key CLI Commands
- `nx run <command>`: Run a command on GPU(s) immediately
- `nx add <command>`: Add command(s) to the job queue
- `nx queue`: Show pending jobs
- `nx logs [job_id]`: View job logs
- `nx attach [job_id]`: Attach to running job's screen session
- `nx kill [job_id]`: Kill running job(s)
- `nx history`: Show completed/failed jobs
- `nx health`: Display system health metrics
- `nx target <name>`: Switch between local/remote servers
- `nx target add <name> <url>`: Add new remote server target

## Memory & Learning

- Update this file whenever user corrects or provides specific instructions
- Record user's command preferences and workflow patterns
- Proactively remember past corrections and apply them consistently
- Ask if unclear whether a correction should be recorded here

## Programming Paradigm

- **Purely Functional Core**: Implement core logic as pure functions with immutable data models
- **Avoid OOP**: No classes with methods, inheritance, or complex object hierarchies
- **Dataclasses Only**: Use frozen dataclasses for data structures, never mutable classes
- **State Flow Pattern**: State changes flow through function returns, never as side effects
- **Function Composition**: Build complex operations by composing smaller pure functions

## Function Design

- **Pure Functions**: No side effects, same output for same input (see `generate_job_id`, `build_job_env`)
- **Function Naming**: Use verb_noun format for function names (`create_job`, `update_job`)
- **Function Size**: Keep functions under 20 lines, extract helpers for logical parts
- **Private Helpers**: Use underscore prefix (`_parse_exit_code`, `_build_script_content`)
- **Function Replacement**: Prefer `dc.replace()` over mutation to modify dataclass instances

## Type System

- **Full Type Annotations**: Use complete type hints for all parameters and return values
- **Union Types**: Use pipe syntax for union types (`str | None`, not `Optional[str]`)
- **Literal Types**: Use `tp.Literal` for constrained string values (`JobStatus = tp.Literal["queued", "running", "completed", "failed"]`)
- **Type Aliases**: Define type aliases for complex types at the module level
- **Return Type Clarity**: Always specify return types, including `None` when appropriate
- **Pyright**: This project uses strict type checking with Pyright
- **Verification**: Always run `uv run pyright` (typically in src directory) before submitting changes
- **No Type Errors**: All code must satisfy Pyright's type checker without errors or warnings

## Import Style

- **Fixed Abbreviations**:
  - `import dataclasses as dc`
  - `import pathlib as pl`
  - `import datetime as dt`
  - `import fastapi as fa`
  - `import typing as tp` (when needed)
- **Explicit Module Imports**: Import from specific modules, not packages
- **Local Import Format**: `from nexus.server.core import exceptions, logger, schemas`

## Common Patterns

### Transaction Pattern
Use `@safe_transaction` decorator for database operations that need rollback on failure:
```python
@safe_transaction
async def operation(ctx: NexusServerContext, ...):
    await db.update_job(ctx.db, job)
    await db.add_artifact(ctx.db, artifact)
```

### Exception Handling Pattern
Use `@handle_exception` to convert technical exceptions to domain exceptions:
```python
@handle_exception(ValueError, DomainError, message="Invalid input")
def validate_input(data: str) -> int:
    return int(data)
```

### Cache Pattern
Module-level `_cache` dict with TTL-based expiration for expensive operations:
```python
_cache: dict[str, tuple[float, Any]] = {}

def cached_operation(force_refresh: bool = False) -> Result:
    cache_key = "operation"
    if not force_refresh and cache_key in _cache:
        timestamp, value = _cache[cache_key]
        if time.time() - timestamp < TTL_SECONDS:
            return value
    result = expensive_operation()
    _cache[cache_key] = (time.time(), result)
    return result
```

### Artifact Lifecycle
- Artifacts are reference-counted: only deleted when no queued jobs reference them
- Upload limit: 50MB per artifact
- Cleanup happens automatically in scheduler after job starts
- Use `is_artifact_in_use()` before deleting artifacts

### Git Integration
- Jobs can auto-create git tags at submission time
- Tag name stored in `Job.git_tag` field
- Environment variable `NEXUS_GIT_TAG` injected into job environment
- Tags pushed before job starts (if tag creation enabled)

### Notification Message Updates
- Discord message IDs stored in `Job.notification_messages` dict
- Allows editing messages when W&B URL discovered
- Pattern: Send "started" message, edit later with W&B URL

## Utils & Helpers

- **Composable Utils**: Small, reusable utility functions
- **Consistent Naming**: Similar operations use similar naming patterns
- **Parameter Order**: Context/logger parameters first, optional params last
- **Default Values**: Use sensible defaults for optional parameters

## Code Documentation - NO COMMENTS POLICY

- 🔴 **NEVER ADD COMMENTS**: Code should be clear enough without them - NO EXCEPTIONS
- 🔴 **ZERO EXPLANATORY COMMENTS**: Do not explain what the code does - the code itself is the explanation
- 🔴 **NO INLINE COMMENTS**: Never add comments next to or above code lines
- 🔴 **NO DESCRIPTIVE COMMENTS**: Never add comments describing what a section does
- 🔴 **NO CODE WALKTHROUGH COMMENTS**: Don't add comments explaining the algorithm or approach
- 🔴 **NO FUNCTION PURPOSE COMMENTS**: Function names and signatures must communicate purpose
- 🔴 **NO FILE PURPOSE COMMENTS**: File organization should make purpose obvious

- ✅ **Self-Documenting Code**: Use clear, descriptive variable and function names
- ✅ **Type-Based Documentation**: Rely on type signatures to document interfaces
- ✅ **Clean Interfaces**: Function names and signatures must be clear enough without comments
- ✅ **Docstrings Restricted**: Only add docstrings when function purpose isn't obvious from name/types

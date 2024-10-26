# Nexus: GPU Job Management System

## 1. Overview

A minimal, reliable system for managing ML experiment jobs across GPUs, consisting of:

- REST API service that:
  - Manages job queue and GPU assignments
  - Runs each job in its own screen session
  - Monitors GPU availability and job status
  - Maintains job history and logs
  - Provides real-time job status and logs
  - Persists state to state.json

- Features:
  - One job per GPU
  - FIFO job queue
  - Job status tracking
  - Configurable job history
  - Environment variable preservation
  - Log capture and streaming
  - GPU auto-assignment
  - Queue pause/resume
  - Service and job persistence via screen
  - Implementable in Rust or Python

## 2. Service Architecture

The nexus service:

- HTTP API server running in screen session named 'nexus'
- Manages job queue and GPU assignments via state.json
- Monitors GPU status via nvidia-smi
- Starts/stops jobs as GPUs become available
- Maintains log of all operations
- Survives terminal disconnects
- Auto-recovers running jobs on service restart

Service Log Format:

```
[2024-10-24 15:30:15] Service started
[2024-10-24 15:30:16] Found 4 GPUs
[2024-10-24 15:30:20] Started job abc on GPU 0: python train.py
[2024-10-24 15:35:25] Job def completed successfully on GPU 1
[2024-10-24 15:35:30] Queue paused by user
```

### Screen Session Management

- Nexus service runs in screen session named 'nexus'
  - Shows continuous service logs
  - Logs include job starts, completions, errors
- Each job runs in screen session named 'nexus_job_<id>'
- Screen sessions persist across terminal disconnects
- Auto-cleanup of job screen sessions on completion
- Ctrl+A+D returns to shell when attached to job/service

## 3. File Structure

```
~/.nexus/
├── logs/
│   └── job_<id>/           # Uses letter-based ID (e.g., abc)
│       ├── stdout.log
│       └── stderr.log
├── state.json              # All system state
└── config.toml
```

### config.toml Format

```toml
[service]
host = "127.0.0.1"
port = 6234
refresh_rate_seconds = 5  # GPU polling interval

[state]
state_file = "~/.nexus/state.json"
log_dir = "~/.nexus/logs"
history_limit = 1000      # Number of completed jobs to retain
```

## 4. Core Functionality

### Job Queue Management

- Jobs are processed FIFO (First In, First Out)
- Jobs start automatically when GPU becomes available
- One job per GPU - no multi-GPU support
- Jobs inherit current environment variables
- CUDA_VISIBLE_DEVICES is automatically set
- Queue can be paused/resumed

### Job Monitoring

- Poll nvidia-smi every 5 seconds for GPU status
- Monitor screen sessions for job status
- Capture stdout/stderr to log files
- Track runtime and start time for all jobs

### Environment Variables

Automatically set for each job:

```bash
CUDA_VISIBLE_DEVICES=${gpu_index}
NEXUS_JOB_ID=${id}
NEXUS_GPU_ID=${gpu_index}
```

### Error Handling

- Corrupted state.json: Continue with last valid state
- GPU unavailable: Skip and try next job
- Screen session dies: Mark job as failed
- System reboot: Restart from clean state
- Invalid command: Show error and usage

## 5. Implementation Notes

### GPU Detection

```bash
# Use nvidia-smi for GPU detection
nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv
```

### Screen Session Commands

```bash
# Start new session
screen -dmS nexus_job_${id} bash -c "${command}"

# Attach to session
screen -r nexus_job_${id}

# Kill session
screen -S nexus_job_${id} -X quit
```

### Job ID Generation

- Use base58 encoding for readable, short IDs
- Format: 6 letters (e.g., 'abcxyz')
- Generated from timestamp + random component
- Must be unique across running and historical jobs

### Implementation Languages

The system can be implemented in either:

- Rust: For maximum performance and reliability
- Python: For easier integration with ML workflows

## 6. CLI Reference

### Basic Commands

```bash
nexus                   # Show status snapshot (non-interactive)
nexus stop              # Stop the nexus service
nexus restart           # Restart the nexus service
nexus add "command"     # Add job(s) to queue (see Job Submission Formats)
nexus queue             # Show pending jobs
nexus history           # Show completed jobs
nexus kill <pattern>    # Kill job(s) by ID, GPU number, or command regex
nexus remove <pattern>  # Remove job(s) from queue by ID or command regex
nexus pause             # Pause queue processing
nexus resume            # Resume queue processing
nexus logs <id>         # View logs for job (running or completed)
nexus logs service      # View service logs
nexus attach <id|gpu>   # Attach to running job's screen session
nexus config            # View current config
nexus config edit       # Edit config.toml in $EDITOR
nexus help              # Show command help and usage
nexus help <command>    # Show detailed help for specific command
```

### Job Submission Formats

The `nexus add` command supports several formats for flexible job submission:

1. Single Command:

```bash
nexus add "python train.py --model gpt2"
```

2. Repeated Command:

```bash
nexus add "python train.py --model gpt2" -r 16  # Adds 16 copies of the command
```

3. Parameter Combinations:

```bash
# Expands to multiple commands with different parameter values
nexus add "KL_WEIGHT={0.01,0.005,0.00001} uv run train.py apollo/fake"

# Multiple parameter sets are supported and all combinations are generated
nexus add "LR={0.1,0.01} BATCH={32,64} python train.py"
```

4. Batched Commands:

```bash
nexus add "python train.py model1|python train.py model2"  # Adds both commands
```

Format Details:

- Curly brackets {} are used to specify parameter sets
- Multiple parameter sets generate all possible combinations
- The -r flag specifies repetition count
- The | character separates batched commands
- Commands with spaces must be quoted
- Parameter values should not contain spaces

### Display Format

The status snapshot (shown by `nexus` command) displays:

```
Queue: 3 jobs pending [PAUSED]     # Shows queue status and processing state
History: 25 jobs completed         # Shows total completed jobs

GPUs:
GPU 0 (RTX 3090, 24GB): Available
GPU 1 (RTX 3090, 24GB):
  Job ID: abc                      # Short letter-based ID (base58)
  Command: python train.py --model gpt2
  Runtime: 2h 15m
  Started: 15:30
GPU 2 (RTX 3090, 24GB): Available
```

Color and Formatting:

- GPU headers in white
- "Available" in bright green
- Job commands in bright white
- Runtime and timestamps in cyan
- Queue status in blue, PAUSE/RUNNING state in yellow
- Error messages in red
- Job IDs in magenta

### CLI Behavior

The CLI handles:

- Starting the service if not running
- Service discovery (using config.toml port)
- API communication
- Screen session attachment
- Configuration file editing

### Command Pattern Matching

The system supports flexible job management through pattern matching:

- Kill/remove by job ID: `nexus kill abc`
- Kill by GPU number: `nexus kill 0`
- Kill/remove by command regex: `nexus kill "train.*gpt2"`
- Multiple matches: `nexus remove ".*bert.*"` (removes all queued jobs with "bert" in command)

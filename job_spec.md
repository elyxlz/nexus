# Nexus: GPU Job Management CLI

A minimal, reliable CLI tool for managing ML experiment jobs across GPUs.

## 1. Core Design Philosophy

- Simple command structure: Intuitive commands without nested subcommands
- Minimal interaction: Clear, single-purpose commands
- Always-on: Runs job management daemon in background
- Single responsibility: One job per GPU
- Complete history: Infinite job logging
- Quick job addition: Simple command to queue jobs

## 2. Command Line Interface

### Startup Behavior

1. When `nexus` is run:
   - Check if 'nexus' screen session exists
     - If not: Create new session and start service
     - If exists: Show status snapshot
   - On first run, create ~/.nexus directory structure
   - Initialize empty jobs.txt if not present
   - Start service in screen session showing service logs

### Basic Commands

```bash
nexus                    # Show status snapshot (non-interactive)
nexus stop              # Stop the nexus service
nexus restart           # Restart the nexus service
nexus add "command"     # Add job to queue
nexus queue             # Show pending jobs
nexus history           # Show completed jobs
nexus kill <id|gpu>     # Kill job by ID or GPU number
nexus remove <id>       # Remove job from queue
nexus pause             # Pause queue processing
nexus resume            # Resume queue processing
nexus logs <id>         # View logs for job (running or completed)
nexus attach <id|gpu>   # Attach to running job's screen session
nexus edit              # Open jobs.txt in $EDITOR
nexus config            # View current config
nexus config edit       # Edit config.toml in $EDITOR
nexus help              # Show command help and usage
nexus help <command>    # Show detailed help for specific command
```

### Screen Session Management

- Nexus service runs in screen session named 'nexus'
  - Shows continuous service logs
  - Logs include job starts, completions, errors
  - Use `nexus attach service` to view service logs
- Each job runs in screen session named 'nexus_job_<id>'
- Screen sessions persist across terminal disconnects
- Auto-cleanup of job screen sessions on completion
- Ctrl+A+D returns to shell when attached to job/service

### Service Management

The nexus service:

- Runs in a screen session named 'nexus'
- Manages job queue and GPU assignments
- Monitors GPU status via nvidia-smi
- Starts/stops jobs as GPUs become available
- Maintains log of all operations
- Survives terminal disconnects
- Auto-recovers running jobs on service restart

Service Commands:

```bash
nexus                # Show status snapshot
nexus stop          # Stop service and all running jobs
nexus restart       # Restart service (preserves running jobs)
nexus attach service # View service logs
```

Service Log Format:

```
[2024-10-24 15:30:15] Service started
[2024-10-24 15:30:16] Found 4 GPUs
[2024-10-24 15:30:20] Started job abc on GPU 0: python train.py
[2024-10-24 15:35:25] Job def completed successfully on GPU 1
[2024-10-24 15:35:30] Queue paused by user
```

### Status View Format

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

## 3. File Structure

```
~/.nexus/
├── logs/
│   └── job_<id>/           # Uses letter-based ID (e.g., abc)
│       ├── stdout.log
│       └── stderr.log
├── jobs.txt
└── config.toml
```

### jobs.txt Format

```
# Each line represents one job
# Format: <command>
WANDB_MODE=disabled TESTING=True python train.py --model gpt2
ENV_VAR=value python eval.py --dataset imagenet
```

### config.toml Format

```toml
[paths]
log_dir = "~/.nexus/logs"
jobs_file = "~/.nexus/jobs.txt"
```

## 4. Command Details

### nexus

- Shows non-interactive status snapshot
- Shows all GPUs, running jobs, and queue status
- One-time display, exits after showing status

### nexus add <command>

- Adds new job to queue
- Preserves environment variables in command
- Returns job ID on success
- Example: `nexus add "python train.py --model gpt2"`

### nexus queue

- Lists all pending jobs with IDs
- Shows position in queue
- Displays full command for each job

### nexus history

- Shows completed jobs chronologically
- Includes runtime, completion status, and GPU used
- Displays job ID and command

### nexus kill <id|gpu>

- Accepts either job ID or GPU number
- Terminates running job
- Cleans up screen session
- Example: `nexus kill abc` or `nexus kill 0`

### nexus remove <id>

- Removes job from queue before execution
- Example: `nexus remove abc`

### nexus logs <id>

- Views logs for any job (running or completed)
- Shows both stdout and stderr
- Supports real-time following for running jobs
- Example: `nexus logs abc`

### nexus attach <id|gpu>

- Attaches to running job's screen session
- Supports both job ID and GPU number
- Use Ctrl+A+D to detach
- Example: `nexus attach abc` or `nexus attach 0`

### nexus edit

- Opens jobs.txt in $EDITOR
- Reloads queue after saving

### nexus config

- Shows current configuration
- `nexus config edit` opens config.toml in $EDITOR

### nexus help

- Shows general help and command list
- `nexus help <command>` shows detailed help for specific command

## 5. Core Functionality

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

- Corrupted jobs.txt: Continue with remaining valid entries
- GPU unavailable: Skip and try next job
- Screen session dies: Mark job as failed
- System reboot: Restart from clean state
- Invalid command: Show error and usage

## 6. Implementation Notes

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
- Format: 3-4 letters (e.g., 'abc', 'xyz')
- Generated from timestamp + random component
- Must be unique across running and historical jobs

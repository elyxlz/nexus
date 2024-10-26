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

## 2. API Endpoints

### System Status

```
GET /status
Response: {
    "gpu_status": [
        {
            "index": 0,
            "name": "RTX 3090",
            "memory_total": "24GB",
            "memory_used": "0GB",
            "job_id": null
        },
        {
            "index": 1,
            "name": "RTX 3090", 
            "memory_total": "24GB",
            "memory_used": "16GB",
            "job_id": "abc"
        }
    ],
    "is_paused": false,
    "queued_jobs": 3,
    "completed_jobs": 25
}
```

### Job Queue Management

```
GET /queue
Response: [
    {
        "id": "abc",
        "command": "python train.py",
        "position": 0,
        "created_at": "2024-10-24T15:30:15Z"
    }
]

POST /queue
Request: {
    "command": "python train.py",
    "env": {"WANDB_MODE": "disabled"}
}
Response: {
    "id": "abc"
}

DELETE /queue/{id}
Response: 204 No Content
```

### Job Management

```
GET /jobs
Response: [
    {
        "id": "abc",
        "command": "python train.py",
        "status": "running",
        ...
    }
]

GET /jobs/{id}
Response: {
    "id": "abc",
    "command": "python train.py",
    "status": "running",
    "gpu_index": 1,
    ...
}

DELETE /jobs/{id}  # Kill running job
Response: 204 No Content

GET /jobs/{id}/logs
Query params:
    follow: bool    # Stream logs
    stream: stdout|stderr|both
Response: Stream of log lines
```

### Service Control

```
POST /service/pause
Response: 204 No Content

POST /service/resume  
Response: 204 No Content

GET /service/logs
Query params:
    follow: bool    # Stream logs
Response: Stream of log lines
```

## 3. Service Architecture

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

## 4. File Structure

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

- Corrupted state.json: Continue with last valid state
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

### Implementation Languages

The system can be implemented in either:

- Rust: For maximum performance and reliability
- Python: For easier integration with ML workflows

## 7. CLI Reference

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
nexus logs <id> [-f]    # View logs for job (running or completed)
nexus logs service [-f] # View or follow service logs
nexus attach <id|gpu>   # Attach to running job's screen session
nexus config            # View current config
nexus config edit       # Edit config.toml in $EDITOR
nexus help              # Show command help and usage
nexus help <command>    # Show detailed help for specific command
```

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
- Log streaming
- Screen session attachment
- Configuration file editing

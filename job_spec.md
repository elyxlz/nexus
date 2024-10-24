# Nexus: GPU Job Management TUI

A minimal, reliable TUI tool for managing ML experiment jobs across GPUs.

## 1. Core Design Philosophy

- Three distinct views: Home, Queue, and History
- Minimal interaction: Simple controls and navigation
- Always-on: Runs in a persistent screen session
- Single responsibility: One job per GPU
- Complete history: Infinite job logging
- Quick job addition: Fast command entry without leaving TUI

## 2. Command Line Interface

### Basic Usage

```bash
nexus    # Start nexus TUI or reattach if running
```

### Startup Behavior

1. Check if 'nexus' screen session exists
   - If exists: Attach to existing session
   - If not: Create new session and launch TUI
2. On first run, create ~/.nexus directory structure
3. Initialize empty jobs.txt if not present
4. Scan GPUs using nvidia-smi
5. Start in Home view

## 3. File Structure

```
~/.nexus/
├── logs/
│   └── job_<timestamp>_<command_hash>/
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

## 4. Views and Navigation

### Global Elements

- Status line at top: "NEXUS - [RUNNING/PAUSED]"
- Control help at bottom showing available commands
- Tab cycles between views
- Up/Down arrows for scrolling in all views
- Ctrl+C returns to nexus TUI when attached to job
- Space toggles pause/resume of job queue
- A opens command entry mode at bottom of screen
- V opens jobs.txt in $EDITOR

### Command Entry Mode

Description:

- Activated with 'A' key from any view
- Shows command prompt at bottom of screen: "Add job > "
- Full-line editing with cursor movement
- Enter submits command to queue
- Esc cancels command entry
- Command is appended to jobs.txt on submit
- Returns to previous view after submit/cancel

### Home View (Default)

Description:

- Lists all available GPUs with their specs (name, memory)
- Shows running job on each GPU if present
- For each running job displays:
  - Command being executed
  - Runtime (e.g., "2h 15m")
  - Time started (e.g., "Started: 15:30")
  - GPU index it's running on

Controls:

- K: Kill selected running job
- Enter: Attach to selected job's screen session

### Queue View

Description:

- Shows all pending jobs in order
- Each entry shows full command to be executed
- Scrollable list if more jobs than screen height

Controls:

- K: Remove selected job from queue
- Enter: Edit selected job's command

### History View

Description:

- Chronological list of completed jobs
- Each entry shows:
  - Command that was executed
  - Runtime
  - Time started
  - GPU it ran on
- Scrollable with infinite history

Controls:

- Enter: View full logs of selected completed job
- K: Delete job from history and remove logs

## 5. Core Functionality

### Job Queue Management

- Jobs are processed FIFO (First In, First Out)
- Jobs start automatically when a GPU becomes available
- One job per GPU - no multi-GPU support
- Jobs inherit current environment variables
- CUDA_VISIBLE_DEVICES is automatically set based on assigned GPU
- Queue can be paused/resumed with Space key
- Jobs can be added via command entry mode or jobs.txt

### Command Entry

- Single-line editor for quick job addition
- Supports cursor movement (left/right arrows)
- Basic line editing (backspace, delete)
- Command history navigation (up/down arrows)
- Environment variables allowed in commands
- Auto-appends to jobs.txt on submit

### Job Monitoring

- Poll nvidia-smi every 5 seconds for GPU status
- Monitor screen sessions for job status
- Capture stdout/stderr to log files
- Track runtime and start time for all jobs

### Screen Session Management

- Main nexus TUI runs in screen session named 'nexus'
- Each job runs in screen session named 'nexus_job_<timestamp>'
- Screen sessions persist across terminal disconnects
- Auto-cleanup of job screen sessions on completion
- Ctrl+C returns to nexus TUI when attached to job

### Error Handling

- Corrupted jobs.txt: Continue with remaining valid entries
- GPU unavailable: Skip and try next job
- Screen session dies: Mark job as completed
- System reboot: Restart from clean state, don't recover jobs
- Invalid command entry: Show error and return to entry mode

## 6. Implementation Notes

### GPU Detection

```rust
// Use nvidia-smi for GPU detection
nvidia-smi --query-gpu=index,name,memory.total,memory.used --format=csv
```

### Screen Session Commands

```bash
# Start new session
screen -dmS nexus_job_${timestamp} bash -c "${command}"

# Attach to session
screen -r nexus_job_${timestamp}

# Kill session
screen -S nexus_job_${timestamp} -X quit
```

### Environment Variables

- Inherit all current environment variables
- Automatically set:

  ```bash
  CUDA_VISIBLE_DEVICES=${gpu_index}
  NEXUS_JOB_ID=${timestamp}
  NEXUS_GPU_ID=${gpu_index}
  ```

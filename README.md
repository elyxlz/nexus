# Nexus: GPU Job Management CLI

A minimal and reliable CLI tool designed to manage and monitor machine learning (ML) experiment jobs across multiple GPUs efficiently. Nexus helps streamline the execution of GPU jobs by handling job queuing, GPU assignments, and logs, ensuring smooth and optimal GPU utilization with minimal user interaction.

## Key Features

- **Job queuing**: Automatically manages and queues jobs to run on available GPUs, one job per GPU at a time.
- **Persistent job management**: Runs as a background service that continuously monitors and schedules jobs.
- **Easy control**: Offers intuitive commands for managing jobs, viewing logs, and handling GPU resources.
- **Comprehensive logging**: Tracks job history and logs all outputs for completed and running jobs.
- **Screen sessions**: Uses screen sessions to allow you to easily manage and reconnect to running jobs.

## Installation

Install Nexus via pip:

```bash
pip install nexusml
```

## Usage

Here are the basic commands to manage your GPU jobs:

### Start Nexus

```bash
nexus
```

Starts the Nexus service, initializes any necessary files, and displays the current GPU status and job queue.

### Queue a New Job

```bash
nexus add "command"
```

Queues a new job for execution on the next available GPU. Example:

```bash
nexus add "python train.py --model gpt2"
```

### View Job Queue

```bash
nexus queue
```

Displays all pending jobs in the queue.

### View Job History

```bash
nexus history
```

Shows a list of completed jobs with details like runtime and completion status.

### Control Jobs

- **Kill a job by ID or GPU**: `nexus kill <id|gpu>`
- **Remove a job from queue**: `nexus remove <id>`

### Monitor Logs

- **View job logs**: `nexus logs <id>` (View logs for a running or completed job)
- **View service logs**: `nexus logs service` (Monitor the background service activity)

### Attach to Job Session

```bash
nexus attach <id|gpu>
```

Attaches to the screen session of a running job, allowing real-time interaction with it.

### Configuration

To view or edit Nexus's configuration settings:

```bash
nexus config
nexus config edit
```

## File Structure

Nexus stores logs and configurations in the `~/.nexus/` directory, which includes:

- **logs/**: Job-specific logs
- **jobs.txt**: A list of queued jobs
- **config.toml**: Configuration file for custom settings

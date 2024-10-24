use chrono;
use sha2::{Digest, Sha256};
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{self, BufRead, BufReader, Write},
    path::{PathBuf},
    process::Command,
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

// Data structures
struct Job {
    id: String,
    command: String,
    start_time: Option<SystemTime>,
    gpu_index: Option<usize>,
    screen_session: Option<String>,
    status: JobStatus,
    log_dir: Option<PathBuf>,
    env_vars: Vec<(String, String)>,
}

struct Config {
    log_dir: PathBuf,
    jobs_file: PathBuf,
    refresh_rate: u64,
}

struct GpuInfo {
    index: usize,
    name: String,
    memory_total: u64,
    memory_used: u64,
}

#[derive(PartialEq)]
enum JobStatus {
    Queued,
    Running,
    Completed,
    Failed,
}

// Config management
fn load_config() -> io::Result<Config> {
    let home = dirs::home_dir()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "Could not find home directory"))?;
    let config_path = home.join(".nexus/config.toml");

    // Create default config if it doesn't exist
    if !config_path.exists() {
        let default_config = r#"[paths]
log_dir = "~/.nexus/logs"
jobs_file = "~/.nexus/jobs.txt"

[display]
refresh_rate = 5  # Status view refresh in seconds
"#;
        fs::write(&config_path, default_config)?;
    }

    // Read and parse config
    let content = fs::read_to_string(&config_path)?;
    let config: toml::Value = toml::from_str(&content).map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Config parse error: {}", e),
        )
    })?;

    let base_dir = home.join(".nexus");

    let log_dir = config
        .get("paths")
        .and_then(|p| p.get("log_dir"))
        .and_then(|l| l.as_str())
        .map(|p| p.replace("~", home.to_str().unwrap()))
        .map(PathBuf::from)
        .unwrap_or_else(|| base_dir.join("logs"));

    let jobs_file = config
        .get("paths")
        .and_then(|p| p.get("jobs_file"))
        .and_then(|l| l.as_str())
        .map(|p| p.replace("~", home.to_str().unwrap()))
        .map(PathBuf::from)
        .unwrap_or_else(|| base_dir.join("jobs.txt"));

    let refresh_rate = config
        .get("display")
        .and_then(|d| d.get("refresh_rate"))
        .and_then(|r| r.as_integer())
        .map(|r| r as u64)
        .unwrap_or(5);

    // Ensure directories exist
    fs::create_dir_all(&log_dir)?;
    if !jobs_file.exists() {
        File::create(&jobs_file)?;
    }

    Ok(Config {
        log_dir,
        jobs_file,
        refresh_rate,
    })
}

// Job management
fn generate_job_id() -> String {
    let timestamp = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let mut hasher = Sha256::new();
    hasher.update(timestamp.to_string());
    let hash = hasher.finalize();
    bs58::encode(&hash[..3]).into_string()
}

fn create_job(command: String) -> Job {
    Job {
        id: generate_job_id(),
        command,
        start_time: None,
        gpu_index: None,
        screen_session: None,
        status: JobStatus::Queued,
        log_dir: None,
        env_vars: Vec::new(),
    }
}

fn start_job(job: &mut Job, gpu_index: usize, config: &Config) -> io::Result<()> {
    let session_name = format!("nexus_job_{}", job.id);
    let log_dir = config.log_dir.join(&job.id);
    fs::create_dir_all(&log_dir)?;

    let mut env_vars = vec![
        ("CUDA_VISIBLE_DEVICES".to_string(), gpu_index.to_string()),
        ("NEXUS_JOB_ID".to_string(), job.id.clone()),
        ("NEXUS_GPU_ID".to_string(), gpu_index.to_string()),
    ];
    env_vars.extend(std::env::vars());

    let command = format!(
        "exec 1> {} 2> {}; {}",
        log_dir.join("stdout.log").display(),
        log_dir.join("stderr.log").display(),
        job.command
    );

    let env_vars_str = env_vars
        .iter()
        .map(|(k, v)| format!("export {}=\"{}\";", k, v))
        .collect::<Vec<_>>()
        .join(" ");

    Command::new("screen")
        .args([
            "-dmS",
            &session_name,
            "bash",
            "-c",
            &format!("{}; {}", env_vars_str, command),
        ])
        .output()?;

    job.start_time = Some(SystemTime::now());
    job.gpu_index = Some(gpu_index);
    job.screen_session = Some(session_name);
    job.status = JobStatus::Running;
    job.log_dir = Some(log_dir);
    job.env_vars = env_vars;

    Ok(())
}

// File operations
fn load_jobs(config: &Config) -> io::Result<Vec<Job>> {
    let file = File::open(&config.jobs_file)?;
    let reader = BufReader::new(file);
    let mut jobs = Vec::new();

    for line in reader.lines() {
        let command = line?;
        if !command.trim().is_empty() && !command.trim().starts_with('#') {
            jobs.push(create_job(command));
        }
    }
    Ok(jobs)
}

fn save_jobs(jobs: &[Job], config: &Config) -> io::Result<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .truncate(true)
        .create(true)
        .open(&config.jobs_file)?;

    for job in jobs.iter().filter(|j| j.status == JobStatus::Queued) {
        writeln!(file, "{}", job.command)?;
    }
    Ok(())
}

// GPU management
fn get_gpu_info() -> io::Result<Vec<GpuInfo>> {
    if env::var("NEXUS_DEV").is_ok() {
        return Ok(vec![
            GpuInfo {
                index: 0,
                name: "Mock GPU 0".to_string(),
                memory_total: 8192,
                memory_used: 2048,
            },
            GpuInfo {
                index: 1,
                name: "Mock GPU 1".to_string(),
                memory_total: 16384,
                memory_used: 4096,
            },
        ]);
    }

    let output = Command::new("nvidia-smi")
        .args([
            "--query-gpu=index,name,memory.total,memory.used",
            "--format=csv,noheader",
        ])
        .output()?;

    if !output.status.success() {
        return Err(io::Error::new(
            io::ErrorKind::Other,
            String::from_utf8_lossy(&output.stderr).to_string(),
        ));
    }

    let mut gpus = Vec::new();
    for line in String::from_utf8_lossy(&output.stdout).lines() {
        let parts: Vec<&str> = line.split(',').collect();
        if parts.len() == 4 {
            gpus.push(GpuInfo {
                index: parts[0].trim().parse().unwrap(),
                name: parts[1].trim().to_string(),
                memory_total: parts[2].trim().replace("MiB", "").parse().unwrap(),
                memory_used: parts[3].trim().replace("MiB", "").parse().unwrap(),
            });
        }
    }
    Ok(gpus)
}

// Screen session management
fn is_job_running(session: &str) -> bool {
    Command::new("screen")
        .args(["-ls", session])
        .output()
        .map(|output| String::from_utf8_lossy(&output.stdout).contains(&format!(".{}", session)))
        .unwrap_or(false)
}

// Service management
fn log_service_event(config: &Config, message: &str) -> io::Result<()> {
    let log_path = config.log_dir.join("service.log");
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)?;

    let now = chrono::Local::now();
    writeln!(file, "[{}] {}", now.format("%Y-%m-%d %H:%M:%S"), message)?;
    Ok(())
}

fn start_service(config: &Config) -> io::Result<()> {
    let session_name = "nexus";
    if !is_job_running(session_name) {
        let service_log = config.log_dir.join("service.log");
        Command::new("screen")
            .args([
                "-dmS",
                session_name,
                "bash",
                "-c",
                &format!("exec 1> {}; nexus daemon", service_log.display()),
            ])
            .output()?;
        println!("Nexus service started");
    } else {
        println!("Nexus service is already running");
    }
    Ok(())
}

fn stop_service() -> io::Result<()> {
    Command::new("screen")
        .args(["-S", "nexus", "-X", "quit"])
        .output()?;
    println!("Nexus service stopped");
    Ok(())
}

// Job processing
fn process_jobs(config: &Config) -> io::Result<()> {
    let mut jobs = load_jobs(config)?;
    let gpus = get_gpu_info()?;

    // Update status of running jobs
    for job in jobs.iter_mut().filter(|j| j.status == JobStatus::Running) {
        if let Some(session) = &job.screen_session {
            if !is_job_running(session) {
                job.status = JobStatus::Completed;
                log_service_event(
                    config,
                    &format!("Job {} completed on GPU {}", job.id, job.gpu_index.unwrap()),
                )?;

                // Archive logs
                if let Some(log_dir) = &job.log_dir {
                    let archive_dir = config.log_dir.join("archived");
                    fs::create_dir_all(&archive_dir)?;

                    let timestamp = SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap()
                        .as_secs();

                    let archive_path =
                        archive_dir.join(format!("job_{}_{}.tar.gz", timestamp, job.id));

                    Command::new("tar")
                        .args([
                            "czf",
                            archive_path.to_str().unwrap(),
                            log_dir.to_str().unwrap(),
                        ])
                        .output()?;
                }
            }
        }
    }

    // Find available GPUs
    let available_gpus: Vec<usize> = gpus
        .iter()
        .map(|g| g.index)
        .filter(|&i| {
            !jobs
                .iter()
                .any(|j| j.status == JobStatus::Running && j.gpu_index == Some(i))
        })
        .collect();

    // Start jobs on available GPUs
    for gpu_index in available_gpus {
        if let Some(job) = jobs.iter_mut().find(|j| j.status == JobStatus::Queued) {
            if let Err(e) = start_job(job, gpu_index, config) {
                eprintln!("Failed to start job {}: {}", job.id, e);
                job.status = JobStatus::Failed;
                log_service_event(
                    config,
                    &format!("Failed to start job {} on GPU {}: {}", job.id, gpu_index, e),
                )?;
            } else {
                log_service_event(
                    config,
                    &format!(
                        "Started job {} on GPU {}: {}",
                        job.id, gpu_index, job.command
                    ),
                )?;
            }
        }
    }

    save_jobs(&jobs, config)?;
    Ok(())
}

fn run_daemon(config: &Config) -> io::Result<()> {
    log_service_event(config, "Service started")?;

    let gpus = get_gpu_info()?;
    log_service_event(config, &format!("Found {} GPUs", gpus.len()))?;

    let mut last_check = SystemTime::now();
    loop {
        // Check if paused
        if config.log_dir.join("paused").exists() {
            thread::sleep(Duration::from_secs(1));
            continue;
        }

        // Only check periodically
        if SystemTime::now()
            .duration_since(last_check)
            .unwrap()
            .as_secs()
            < config.refresh_rate
        {
            thread::sleep(Duration::from_millis(100));
            continue;
        }
        last_check = SystemTime::now();

        // Process jobs
        if let Err(e) = process_jobs(config) {
            log_service_event(config, &format!("Error processing jobs: {}", e))?;
        }
    }
}

// Command handlers
fn handle_status(config: &Config) -> io::Result<()> {
    let jobs = load_jobs(config)?;
    let gpus = get_gpu_info()?;

    println!(
        "Queue: {} jobs pending {}",
        jobs.iter()
            .filter(|j| j.status == JobStatus::Queued)
            .count(),
        if config.log_dir.join("paused").exists() {
            "[PAUSED]"
        } else {
            "[RUNNING]"
        }
    );
    println!(
        "History: {} jobs completed\n",
        jobs.iter()
            .filter(|j| j.status == JobStatus::Completed)
            .count()
    );

    println!("GPUs:");
    for gpu in gpus {
        println!("GPU {} ({}, {}MB):", gpu.index, gpu.name, gpu.memory_total);
        if let Some(job) = jobs
            .iter()
            .find(|j| j.status == JobStatus::Running && j.gpu_index == Some(gpu.index))
        {
            let runtime = job
                .start_time
                .map(|t| t.elapsed().unwrap_or_default())
                .unwrap_or_default();
            println!(
                "  Job ID: {}\n  Command: {}\n  Runtime: {}m {}s",
                job.id,
                job.command,
                runtime.as_secs() / 60,
                runtime.as_secs() % 60
            );
        } else {
            println!("  Available");
        }
    }
    Ok(())
}

fn handle_add(command: &str, config: &Config) -> io::Result<()> {
    let mut jobs = load_jobs(config)?;
    let job = create_job(command.to_string());
    println!("Added job {} to queue", job.id);
    jobs.push(job);
    save_jobs(&jobs, config)
}

fn handle_queue(config: &Config) -> io::Result<()> {
    let jobs = load_jobs(config)?;
    for job in jobs.iter().filter(|j| j.status == JobStatus::Queued) {
        println!("{}: {}", job.id, job.command);
    }
    Ok(())
}

fn handle_history(config: &Config) -> io::Result<()> {
    let jobs = load_jobs(config)?;
    for job in jobs.iter().filter(|j| j.status == JobStatus::Completed) {
        let runtime = job
            .start_time
            .map(|t| t.elapsed().unwrap_or_default())
            .unwrap_or_default();
        println!(
            "{}: {} (Runtime: {}m {}s, GPU: {})",
            job.id,
            job.command,
            runtime.as_secs() / 60,
            runtime.as_secs() % 60,
            job.gpu_index.unwrap_or(0)
        );
    }
    Ok(())
}

fn handle_kill(target: &str, config: &Config) -> io::Result<()> {
    let mut jobs = load_jobs(config)?;

    // Try as GPU index first
    if let Ok(gpu_index) = target.parse::<usize>() {
        if let Some(job) = jobs
            .iter_mut()
            .find(|j| j.status == JobStatus::Running && j.gpu_index == Some(gpu_index))
        {
            if let Some(session) = &job.screen_session {
                Command::new("screen")
                    .args(["-S", session, "-X", "quit"])
                    .output()?;
                job.status = JobStatus::Completed;
                println!("Killed job {} on GPU {}", job.id, gpu_index);
                save_jobs(&jobs, config)?;
                return Ok(());
            }
        }
    }

    // Try as job ID
    if let Some(job) = jobs.iter_mut().find(|j| j.id == target) {
        if let Some(session) = &job.screen_session {
            Command::new("screen")
                .args(["-S", session, "-X", "quit"])
                .output()?;
            job.status = JobStatus::Completed;
            println!("Killed job {}", job.id);
            save_jobs(&jobs, config)?;
            return Ok(());
        }
    }

    println!("No running job found with ID or GPU: {}", target);
    Ok(())
}

fn handle_remove(id: &str, config: &Config) -> io::Result<()> {
    let mut jobs = load_jobs(config)?;
    if let Some(pos) = jobs
        .iter()
        .position(|j| j.id == id && j.status == JobStatus::Queued)
    {
        jobs.remove(pos);
        println!("Removed job {} from queue", id);
        save_jobs(&jobs, config)?;
    } else {
        println!("No queued job found with ID: {}", id);
    }
    Ok(())
}

fn handle_logs(id: &str, config: &Config) -> io::Result<()> {
    let jobs = load_jobs(config)?;
    if let Some(job) = jobs.iter().find(|j| j.id == id) {
        if let Some(log_dir) = &job.log_dir {
            println!("=== STDOUT ===");
            if let Ok(content) = fs::read_to_string(log_dir.join("stdout.log")) {
                println!("{}", content);
            }

            println!("\n=== STDERR ===");
            if let Ok(content) = fs::read_to_string(log_dir.join("stderr.log")) {
                println!("{}", content);
            }
        } else {
            println!("No logs found for job {}", id);
        }
    } else {
        println!("No job found with ID: {}", id);
    }
    Ok(())
}

fn handle_attach(target: &str) -> io::Result<()> {
    let session_name = if let Ok(gpu_index) = target.parse::<usize>() {
        format!("nexus_job_gpu_{}", gpu_index)
    } else {
        format!("nexus_job_{}", target)
    };

    if is_job_running(&session_name) {
        Command::new("screen")
            .args(["-r", &session_name])
            .status()?;
        Ok(())
    } else {
        println!("No running job found for {}", target);
        Ok(())
    }
}

fn handle_config(config: &Config) -> io::Result<()> {
    let home = dirs::home_dir().unwrap();
    let config_path = home.join(".nexus/config.toml");
    let content = fs::read_to_string(&config_path)?;
    println!("Current configuration:\n{}", content);
    Ok(())
}

fn handle_config_edit() -> io::Result<()> {
    let home = dirs::home_dir().unwrap();
    let config_path = home.join(".nexus/config.toml");
    let editor = env::var("EDITOR").unwrap_or_else(|_| "vim".to_string());
    Command::new(editor).arg(&config_path).status()?;
    Ok(())
}

fn print_help() {
    println!(
        r#"Nexus: GPU Job Management CLI

USAGE:
    nexus                     Show status
    nexus stop               Stop the nexus service
    nexus restart            Restart the nexus service
    nexus add "command"      Add job to queue
    nexus queue              Show pending jobs
    nexus history            Show completed jobs
    nexus kill <id|gpu>      Kill job by ID or GPU number
    nexus remove <id>        Remove job from queue
    nexus pause              Pause queue processing
    nexus resume             Resume queue processing
    nexus logs <id>          View logs for job
    nexus attach <id|gpu>    Attach to running job's screen session
    nexus edit               Open jobs.txt in $EDITOR
    nexus config             View current config
    nexus config edit        Edit config.toml in $EDITOR
    nexus help               Show this help
    nexus help <command>     Show detailed help for command"#
    );
}

fn print_command_help(command: &str) {
    match command {
        "add" => println!("nexus add \"command\"\nAdd a new job to the queue. Enclose command in quotes."),
        "kill" => println!("nexus kill <id|gpu>\nKill a running job by its ID or GPU number."),
        "attach" => println!("nexus attach <id|gpu>\nAttach to a running job's screen session. Use Ctrl+A+D to detach."),
        "config" => println!("nexus config\nView current configuration.\nnexus config edit\nEdit configuration in $EDITOR."),
        _ => println!("No detailed help available for: {}", command),
    }
}

fn main() -> io::Result<()> {
    let config = load_config()?;
    let args: Vec<String> = env::args().collect();

    match args.get(1).map(|s| s.as_str()) {
        None => {
            // Check/start service first
            start_service(&config)?;
            handle_status(&config)
        }
        Some("stop") => stop_service(),
        Some("restart") => {
            stop_service()?;
            thread::sleep(Duration::from_secs(1));
            start_service(&config)
        }
        Some("add") => {
            if args.len() < 3 {
                println!("Usage: nexus add \"command\"");
                Ok(())
            } else {
                handle_add(&args[2..].join(" "), &config)
            }
        }
        Some("queue") => handle_queue(&config),
        Some("history") => handle_history(&config),
        Some("kill") => {
            if args.len() < 3 {
                println!("Usage: nexus kill <id|gpu>");
                Ok(())
            } else {
                handle_kill(&args[2], &config)
            }
        }
        Some("remove") => {
            if args.len() < 3 {
                println!("Usage: nexus remove <id>");
                Ok(())
            } else {
                handle_remove(&args[2], &config)
            }
        }
        Some("pause") => {
            fs::write(config.log_dir.join("paused"), "")?;
            println!("Queue processing paused");
            Ok(())
        }
        Some("resume") => {
            fs::remove_file(config.log_dir.join("paused"))?;
            println!("Queue processing resumed");
            Ok(())
        }
        Some("logs") => {
            if args.len() < 3 {
                println!("Usage: nexus logs <id>");
                Ok(())
            } else {
                handle_logs(&args[2], &config)
            }
        }
        Some("attach") => {
            if args.len() < 3 {
                println!("Usage: nexus attach <id|gpu>");
                Ok(())
            } else {
                handle_attach(&args[2])
            }
        }
        Some("edit") => {
            let editor = env::var("EDITOR").unwrap_or_else(|_| "vim".to_string());
            Command::new(editor).arg(&config.jobs_file).status()?;
            Ok(())
        }
        Some("config") => {
            if args.len() > 2 && args[2] == "edit" {
                handle_config_edit()
            } else {
                handle_config(&config)
            }
        }
        Some("daemon") => run_daemon(&config),
        Some("help") => {
            if args.len() > 2 {
                print_command_help(&args[2]);
            } else {
                print_help();
            }
            Ok(())
        }
        Some(cmd) => {
            println!("Unknown command: {}", cmd);
            print_help();
            Ok(())
        }
    }
}

use chrono::{DateTime, Local};
use colored::*;
use humantime::format_duration;
use sha2::{Digest, Sha256};
use std::{
    env,
    fs::{self, File, OpenOptions},
    io::{self, BufRead, BufReader, Write},
    path::PathBuf,
    process::Command,
    thread,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

// Data structures
#[derive(Clone, Debug)]
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
    datetime_format: String,
}

#[derive(Debug)]
struct GpuInfo {
    index: usize,
    name: String,
    memory_total: u64,
    memory_used: u64,
}

#[derive(Clone, Debug, PartialEq)]
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
refresh_rate = 10  # Status view refresh in seconds
datetime_format = "%Y-%m-%d %H:%M:%S"
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

    let datetime_format = config
        .get("display")
        .and_then(|d| d.get("datetime_format"))
        .and_then(|f| f.as_str())
        .unwrap_or("%Y-%m-%d %H:%M:%S")
        .to_string();

    // Ensure directories exist
    fs::create_dir_all(&log_dir)?;
    if !jobs_file.exists() {
        File::create(&jobs_file)?;
    }

    Ok(Config {
        log_dir,
        jobs_file,
        refresh_rate,
        datetime_format,
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
    env_vars.extend(std::env::vars().filter(|(k, _)| !k.starts_with("SCREEN_")));

    let command = format!(
        "exec 1> {} 2> {}; {}",
        log_dir.join("stdout.log").display(),
        log_dir.join("stderr.log").display(),
        job.command
    );

    let env_vars_str = env_vars
        .iter()
        .map(|(k, v)| format!("export {}=\"{}\";", k, v.replace("\"", "\\\"")))
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

    // Log job start
    log_service_event(
        config,
        &format!("Job {} started on GPU {}", job.id, gpu_index),
    )?;

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

    // Load running jobs from screen sessions
    let running_jobs = recover_running_jobs()?;
    jobs.extend(running_jobs);

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

// Recovery
fn recover_running_jobs() -> io::Result<Vec<Job>> {
    let output = Command::new("screen").args(["-ls"]).output()?;
    let screen_output = String::from_utf8_lossy(&output.stdout);
    let mut jobs = Vec::new();

    for line in screen_output.lines() {
        if let Some(session_name) = line
            .split_whitespace()
            .find(|&s| s.starts_with("nexus_job_"))
        {
            let job_id = session_name.trim_start_matches("nexus_job_");
            let gpu_index = Command::new("ps")
                .args(["aux"])
                .output()
                .ok()
                .and_then(|output| {
                    String::from_utf8_lossy(&output.stdout)
                        .lines()
                        .find(|line| line.contains(session_name))
                        .and_then(|line| {
                            line.split_whitespace()
                                .find(|&s| s.starts_with("CUDA_VISIBLE_DEVICES="))
                                .and_then(|s| s.split('=').nth(1))
                                .and_then(|s| s.parse().ok())
                        })
                });

            if let Some(gpu_idx) = gpu_index {
                let mut job = create_job(String::new()); // Command will be empty for recovered jobs
                job.id = job_id.to_string();
                job.gpu_index = Some(gpu_idx);
                job.screen_session = Some(session_name.to_string());
                job.status = JobStatus::Running;
                jobs.push(job);
            }
        }
    }

    Ok(jobs)
}

// Service management
fn log_service_event(config: &Config, message: &str) -> io::Result<()> {
    let log_path = config.log_dir.join("service.log");
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(log_path)?;

    let now = Local::now();
    writeln!(
        file,
        "[{}] {}",
        now.format(&config.datetime_format),
        message
    )?;
    Ok(())
}

fn start_service(config: &Config) -> io::Result<()> {
    let session_name = "nexus";

    if !is_job_running(session_name) {
        let service_log = config.log_dir.join("service.log");
        let output = Command::new("screen")
            .args([
                "-dmS",
                session_name,
                "bash",
                "-c",
                &format!(
                    "exec 1> {} 2> {}; nexus service",
                    service_log.display(),
                    service_log.display()
                ),
            ])
            .output()?;

        if !output.status.success() {
            eprintln!("Failed to start screen session: {:?}", output);
        } else {
            println!("{}", "Nexus service started".green());
            log_service_event(config, "Nexus service started")?; // Log service start
        }
    } else {
        println!("{}", "Nexus service is already running".yellow());
    }
    Ok(())
}

fn nexus_service(config: &Config) -> io::Result<()> {
    loop {
        // Poll nvidia-smi to check for available GPUs
        let gpus = get_gpu_info()?;

        // Load jobs from the queue
        let mut jobs = load_jobs(config)?;

        // Collect available GPUs first
        let available_gpus: Vec<&GpuInfo> =
            gpus.iter().filter(|g| is_gpu_available(g, &jobs)).collect();

        // Assign jobs to available GPUs
        for gpu in available_gpus {
            if let Some(job) = jobs.iter_mut().find(|j| j.status == JobStatus::Queued) {
                if let Err(e) = start_job(job, gpu.index, config) {
                    eprintln!("{}", format!("Failed to start job {}: {}", job.id, e).red());
                    job.status = JobStatus::Failed;
                }
            }
        }

        // Save the job state
        save_jobs(&jobs, config)?;

        // Sleep for 5 seconds before polling again
        std::thread::sleep(std::time::Duration::from_secs(config.refresh_rate));
    }
}

fn is_gpu_available(gpu: &GpuInfo, jobs: &[Job]) -> bool {
    // Check if there is any running job that is using this GPU
    !jobs
        .iter()
        .any(|job| job.status == JobStatus::Running && job.gpu_index == Some(gpu.index))
}

fn stop_service(config: &Config) -> io::Result<()> {
    Command::new("screen")
        .args(["-S", "nexus", "-X", "quit"])
        .output()?;
    println!("{}", "Nexus service stopped".green());
    log_service_event(config, "Nexus service stopped")?; // Log service stop
    Ok(())
}

// Handles
fn handle_status(config: &Config) -> io::Result<()> {
    // Show the queue, history, and current GPU status without starting the service
    let jobs = load_jobs(config)?;
    let gpus = get_gpu_info()?;

    let queued_count = jobs
        .iter()
        .filter(|j| j.status == JobStatus::Queued)
        .count();
    let completed_count = jobs
        .iter()
        .filter(|j| j.status == JobStatus::Completed)
        .count();

    let is_paused = config.log_dir.join("paused").exists();
    let queue_status = if is_paused {
        "PAUSED".yellow()
    } else {
        "RUNNING".green()
    };

    println!(
        "{}: {} jobs pending [{}]",
        "Queue".blue().bold(),
        queued_count,
        queue_status
    );
    println!(
        "{}: {} jobs completed\n",
        "History".blue().bold(),
        completed_count
    );

    println!("{}:", "GPUs".white().bold());
    for gpu in gpus {
        let mem_usage = (gpu.memory_used as f64 / gpu.memory_total as f64 * 100.0) as u64;
        println!(
            "GPU {} ({}, {}MB/{}MB, {}%):",
            gpu.index.to_string().white(),
            gpu.name,
            gpu.memory_used,
            gpu.memory_total,
            mem_usage
        );

        if let Some(job) = jobs
            .iter()
            .find(|j| j.status == JobStatus::Running && j.gpu_index == Some(gpu.index))
        {
            let runtime = job.start_time.map(|t| t.elapsed().unwrap_or_default());
            let start_time = job
                .start_time
                .map(|t| {
                    DateTime::<Local>::from(t)
                        .format(&config.datetime_format)
                        .to_string()
                })
                .unwrap_or_else(|| "Unknown".to_string());

            println!("  {}: {}", "Job ID".magenta(), job.id);
            println!("  {}: {}", "Command".white().bold(), job.command);
            println!(
                "  {}: {}",
                "Runtime".cyan(),
                format_duration(runtime.expect("Expected runtime"))
                    .to_string()
                    .cyan()
            );
            println!("  {}: {}", "Started".cyan(), start_time.cyan());
        } else {
            println!("  {}", "Available".bright_green());
        }
    }

    Ok(())
}

// Command handlers
fn handle_add(command: &str, config: &Config) -> io::Result<()> {
    let mut jobs = load_jobs(config)?;

    let job = create_job(command.to_string());
    let added_time = Local::now().format(&config.datetime_format).to_string();

    println!(
        "{} {}",
        "Added job".green(),
        job.id.to_string().magenta().bold()
    );
    println!("{}: {}", "Command".white().bold(), job.command.cyan());
    println!("{}: {}", "Time Added".white().bold(), added_time.cyan());
    println!("{}", "The job has been added to the queue.".green());

    // Log job addition
    log_service_event(
        config,
        &format!("Job {} added to queue: {}", job.id, job.command),
    )?;

    jobs.push(job);
    save_jobs(&jobs, config)
}

fn handle_queue(config: &Config) -> io::Result<()> {
    let jobs = load_jobs(config)?;
    let queued_jobs: Vec<_> = jobs
        .iter()
        .filter(|j| j.status == JobStatus::Queued)
        .collect();

    println!("{}", "Pending Jobs:".blue().bold());
    for (pos, job) in queued_jobs.iter().enumerate() {
        println!(
            "{}. {} - {}",
            (pos + 1).to_string().blue(),
            job.id.magenta(),
            job.command.white()
        );
    }
    Ok(())
}

fn handle_history(config: &Config) -> io::Result<()> {
    let jobs = load_jobs(config)?;
    println!("{}", "Completed Jobs:".blue().bold());
    for job in jobs.iter().filter(|j| j.status == JobStatus::Completed) {
        let runtime = job
            .start_time
            .map(|t| t.elapsed().unwrap_or_default())
            .unwrap_or_default();
        println!(
            "{}: {} (Runtime: {}, GPU: {})",
            job.id.magenta(),
            job.command.white(),
            format_duration(runtime).to_string().cyan(),
            job.gpu_index
                .map(|i| i.to_string())
                .unwrap_or_else(|| "Unknown".to_string())
                .yellow()
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
                println!(
                    "{} {} {}",
                    "Killed job".green(),
                    job.id.magenta(),
                    format!("on GPU {}", gpu_index).yellow()
                );
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
            println!("{} {}", "Killed job".green(), job.id.magenta());
            save_jobs(&jobs, config)?;
            return Ok(());
        }
    }

    println!(
        "{}",
        format!("No running job found with ID or GPU: {}", target).red()
    );
    Ok(())
}

fn handle_remove(id: &str, config: &Config) -> io::Result<()> {
    let mut jobs = load_jobs(config)?;
    if let Some(pos) = jobs
        .iter()
        .position(|j| j.id == id && j.status == JobStatus::Queued)
    {
        jobs.remove(pos);
        println!("{} {}", "Removed job".green(), id.magenta());
        save_jobs(&jobs, config)?;
    } else {
        println!("{}", format!("No queued job found with ID: {}", id).red());
    }
    Ok(())
}

fn handle_logs(id: &str, config: &Config) -> io::Result<()> {
    let jobs = load_jobs(config)?;
    if let Some(job) = jobs.iter().find(|j| j.id == id) {
        if let Some(log_dir) = &job.log_dir {
            println!("{}", "=== STDOUT ===".blue().bold());
            if let Ok(content) = fs::read_to_string(log_dir.join("stdout.log")) {
                println!("{}", content);
            }

            println!("\n{}", "=== STDERR ===".red().bold());
            if let Ok(content) = fs::read_to_string(log_dir.join("stderr.log")) {
                println!("{}", content);
            }
        } else {
            println!("{}", format!("No logs found for job {}", id).red());
        }
    } else {
        println!("{}", format!("No job found with ID: {}", id).red());
    }
    Ok(())
}

fn handle_service_logs(config: &Config) -> io::Result<()> {
    // Get the path to the service log file
    let log_path = config.log_dir.join("service.log");

    // Check if the log file exists
    if !log_path.exists() {
        println!("{}", "No service log found.".red());
        return Ok(());
    }

    // Read and print the contents of the log file
    let content = fs::read_to_string(&log_path)?;
    println!("{}", content);

    Ok(())
}

fn handle_attach(target: &str) -> io::Result<()> {
    let session_name = if target == "service" {
        "nexus".to_string()
    } else if let Ok(gpu_index) = target.parse::<usize>() {
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
        println!(
            "{}",
            format!("No running session found for {}", target).red()
        );
        Ok(())
    }
}

fn handle_attach_service() -> io::Result<()> {
    if is_job_running("nexus") {
        Command::new("screen").args(["-r", "nexus"]).status()?;
    } else {
        println!("{}", "No running nexus service found.".red());
    }
    Ok(())
}

fn handle_config(_config: &Config) -> io::Result<()> {
    let home = dirs::home_dir().unwrap();
    let config_path = home.join(".nexus/config.toml");
    let content = fs::read_to_string(&config_path)?;
    println!("{}:\n{}", "Current configuration".blue().bold(), content);
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
        "{}

{}:
    nexus                     Show status
    nexus stop               Stop the nexus service
    nexus restart            Restart the nexus service
    nexus add \"command\"      Add job to queue
    nexus queue              Show pending jobs
    nexus history            Show completed jobs
    nexus kill <id|gpu>      Kill job by ID or GPU number
    nexus remove <id>        Remove job from queue
    nexus pause              Pause queue processing
    nexus resume             Resume queue processing
    nexus logs <id>          View logs for job
    nexus logs service       View or follow service logs
    nexus attach <id|gpu>    Attach to running job's screen session
    nexus attach service     Attach to the nexus service session
    nexus edit               Open jobs.txt in $EDITOR
    nexus config             View current config
    nexus config edit        Edit config.toml in $EDITOR
    nexus help               Show this help
    nexus help <command>     Show detailed help for command",
        "Nexus: GPU Job Management CLI".green().bold(),
        "USAGE".blue().bold()
    );
}

fn print_command_help(command: &str) {
    match command {
        "add" => println!(
            "{}\nAdd a new job to the queue. Enclose command in quotes.",
            "nexus add \"command\"".green()
        ),
        "kill" => println!(
            "{}\nKill a running job by its ID or GPU number.",
            "nexus kill <id|gpu>".green()
        ),
        "attach" => println!(
            "{}\nAttach to a running job's screen session. Use Ctrl+A+D to detach.",
            "nexus attach <id|gpu>".green()
        ),
        "config" => println!(
            "{}\n{}\nView current configuration.\n{}\nEdit configuration in $EDITOR.",
            "Configuration:".blue().bold(),
            "nexus config".green(),
            "nexus config edit".green()
        ),
        _ => println!(
            "{}",
            format!("No detailed help available for: {}", command).red()
        ),
    }
}

fn main() -> io::Result<()> {
    let config = load_config()?;
    let args: Vec<String> = env::args().collect();

    match args.get(1).map(|s| s.as_str()) {
        None => {
            start_service(&config)?; // Starts the service in the screen session
            handle_status(&config) // Shows the current status
        }
        Some("service") => {
            nexus_service(&config)?; // Continuously runs the nexus background service
            Ok(())
        }
        Some("stop") => stop_service(&config),
        Some("restart") => {
            stop_service(&config)?;
            thread::sleep(Duration::from_secs(1));
            start_service(&config)
        }
        Some("add") => {
            if args.len() < 3 {
                println!("{}", "Usage: nexus add \"command\"".red());
                Ok(())
            } else {
                handle_add(&args[2..].join(" "), &config)
            }
        }
        Some("queue") => handle_queue(&config),
        Some("history") => handle_history(&config),
        Some("kill") => {
            if args.len() < 3 {
                println!("{}", "Usage: nexus kill <id|gpu>".red());
                Ok(())
            } else {
                handle_kill(&args[2], &config)
            }
        }
        Some("remove") => {
            if args.len() < 3 {
                println!("{}", "Usage: nexus remove <id>".red());
                Ok(())
            } else {
                handle_remove(&args[2], &config)
            }
        }
        Some("pause") => {
            fs::write(config.log_dir.join("paused"), "")?;
            println!("{}", "Queue processing paused".yellow());
            Ok(())
        }
        Some("resume") => {
            fs::remove_file(config.log_dir.join("paused"))?;
            println!("{}", "Queue processing resumed".green());
            Ok(())
        }
        Some("logs") => {
            if args.len() < 3 {
                println!("{}", "Usage: nexus logs <id|service> [-f]".red());
                Ok(())
            } else if args[2] == "service" {
                handle_service_logs(&config)
            } else {
                handle_logs(&args[2], &config)
            }
        }
        Some("attach") => {
            if args.len() < 3 {
                println!("{}", "Usage: nexus attach <id|gpu|service>".red());
                Ok(())
            } else if args[2] == "service" {
                handle_attach_service()
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
        Some("help") => {
            if args.len() > 2 {
                print_command_help(&args[2]);
            } else {
                print_help();
            }
            Ok(())
        }
        Some(cmd) => {
            println!("{}", format!("Unknown command: {}", cmd).red());
            print_help();
            Ok(())
        }
    }
}

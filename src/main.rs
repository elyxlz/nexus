// main.rs
use chrono::{DateTime, Local};
use crossterm::{
    event::{self, Event, KeyCode},
    execute,
    terminal::{EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Direction, Layout, Rect},
    style::{Color, Style},
    text::Text,
    widgets::{Block, Borders, List, ListItem, ListState, Paragraph, Wrap},
    Terminal,
};
use sha2::{Digest, Sha256};
use std::{
    collections::VecDeque,
    fs::{self, File, OpenOptions},
    io::{self, BufRead, BufReader, Read, Write},
    path::{Path, PathBuf},
    process::Command,
    time::{Duration, SystemTime},
};

// Data structures
struct Job {
    command: String,
    start_time: Option<SystemTime>,
    gpu_index: Option<usize>,
    screen_session: Option<String>,
    status: JobStatus,
    log_dir: Option<PathBuf>,
    command_hash: String,
    env_vars: Vec<(String, String)>,
}

struct CommandHistory {
    entries: VecDeque<String>,
    position: Option<usize>,
    max_entries: usize,
}

// Add Config struct
struct Config {
    log_dir: PathBuf,
    jobs_file: PathBuf,
}

struct AppState {
    jobs: Vec<Job>,
    gpu_info: Vec<GpuInfo>,
    current_view: View,
    list_state: ListState, // Changed from selected_index: usize
    is_paused: bool,
    command_input: String,
    is_command_mode: bool,
    command_history: CommandHistory,
    show_logs: Option<PathBuf>,
    base_path: PathBuf,
    error_message: Option<String>,
    last_gpu_check: SystemTime,
    config: Config, // Added config field
}

enum JobStatus {
    Queued,
    Running,
    Completed,
}

struct GpuInfo {
    index: usize,
    name: String,
    memory_total: u64,
    memory_used: u64,
}

enum View {
    Home,
    Queue,
    History,
}

// File paths
fn get_nexus_paths() -> (PathBuf, PathBuf) {
    let home = std::env::var("HOME").unwrap();
    let base_path = PathBuf::from(&home).join(".nexus");
    let jobs_file = base_path.join("jobs.txt");
    let log_dir = base_path.join("logs");
    (jobs_file, log_dir)
}

// GPU Management
fn get_gpu_info() -> io::Result<Vec<GpuInfo>> {
    let output = Command::new("nvidia-smi")
        .args([
            "--query-gpu=index,name,memory.total,memory.used",
            "--format=csv,noheader",
        ])
        .output()
        .map_err(|e| {
            io::Error::new(
                io::ErrorKind::Other,
                format!("Failed to execute nvidia-smi: {}", e),
            )
        })?;

    if !output.status.success() {
        return Err(io::Error::new(
            io::ErrorKind::Other,
            format!(
                "nvidia-smi failed: {}",
                String::from_utf8_lossy(&output.stderr)
            ),
        ));
    }

    let output_str = String::from_utf8_lossy(&output.stdout);
    let mut gpus = Vec::new();

    for line in output_str.lines() {
        let parts: Vec<&str> = line.split(',').collect();
        if parts.len() == 4 {
            gpus.push(GpuInfo {
                index: parts[0].trim().parse().map_err(|e| {
                    io::Error::new(
                        io::ErrorKind::InvalidData,
                        format!("Invalid GPU index: {}", e),
                    )
                })?,
                name: parts[1].trim().to_string(),
                memory_total: parts[2].trim().replace("MiB", "").parse().map_err(|e| {
                    io::Error::new(
                        io::ErrorKind::InvalidData,
                        format!("Invalid memory total: {}", e),
                    )
                })?,
                memory_used: parts[3].trim().replace("MiB", "").parse().map_err(|e| {
                    io::Error::new(
                        io::ErrorKind::InvalidData,
                        format!("Invalid memory used: {}", e),
                    )
                })?,
            });
        }
    }

    Ok(gpus)
}

// Job Management

fn create_job(command: String) -> Job {
    let mut hasher = Sha256::new();
    hasher.update(&command);
    let command_hash = format!("{:x}", hasher.finalize());

    Job {
        command,
        start_time: None,
        gpu_index: None,
        screen_session: None,
        status: JobStatus::Queued,
        log_dir: None,
        command_hash: command_hash[..8].to_string(),
        env_vars: Vec::new(),
    }
}

fn start_job(job: &mut Job, gpu_index: usize, base_path: &Path) -> io::Result<()> {
    let timestamp = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let session_name = format!("nexus_job_{}", timestamp);
    let log_dir = create_log_directory(job, base_path)?;

    // Prepare environment variables
    let mut env_vars = vec![
        ("CUDA_VISIBLE_DEVICES".to_string(), gpu_index.to_string()),
        ("NEXUS_JOB_ID".to_string(), timestamp.to_string()),
        ("NEXUS_GPU_ID".to_string(), gpu_index.to_string()),
    ];
    env_vars.extend(std::env::vars());
    job.env_vars = env_vars.clone();

    // Create command with logging
    let command = format!(
        "exec 1> {} 2> {}; {}",
        log_dir.join("stdout.log").display(),
        log_dir.join("stderr.log").display(),
        job.command
    );

    // Start screen session with environment variables
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

    Ok(())
}

fn kill_job(job: &mut Job) -> io::Result<()> {
    if let Some(session) = &job.screen_session {
        Command::new("screen")
            .args(["-S", session, "-X", "quit"])
            .output()?;
        job.status = JobStatus::Completed;
    }
    Ok(())
}

// UI Rendering
fn draw_ui(state: &AppState, frame: &mut ratatui::Frame) {
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(1), // Status line
            Constraint::Min(1),    // Main content
            Constraint::Length(1), // Controls
        ])
        .split(frame.size());

    // Status line
    let status = format!(
        "NEXUS - [{}]",
        if state.is_paused { "PAUSED" } else { "RUNNING" }
    );
    frame.render_widget(Paragraph::new(status), chunks[0]);

    // Main content based on current view
    match state.current_view {
        View::Home => draw_home_view(state, frame, chunks[1]),
        View::Queue => draw_queue_view(state, frame, chunks[1]),
        View::History => draw_history_view(state, frame, chunks[1]),
    }

    // Controls
    let controls = match state.current_view {
        View::Home => "K: Kill job | Enter: Attach | Tab: Switch view | A: Add job",
        View::Queue => "K: Remove job | Enter: Edit | Tab: Switch view | A: Add job",
        View::History => "Enter: View logs | K: Delete | Tab: Switch view | A: Add job",
    };
    frame.render_widget(Paragraph::new(controls), chunks[2]);
}

// Update list state handling in draw functions
fn draw_home_view(state: &AppState, frame: &mut ratatui::Frame, area: Rect) {
    let items: Vec<ListItem> = state
        .gpu_info
        .iter()
        .enumerate()
        .map(|(i, gpu)| {
            let running_job = state
                .jobs
                .iter()
                .find(|j| matches!(j.status, JobStatus::Running) && j.gpu_index == Some(i));

            let job_info = if let Some(job) = running_job {
                let runtime = job
                    .start_time
                    .map(|t| t.elapsed().unwrap_or_default())
                    .unwrap_or_default();
                format!(
                    "{}\nRuntime: {}m {}s",
                    job.command,
                    runtime.as_secs() / 60,
                    runtime.as_secs() % 60
                )
            } else {
                "No job running".to_string()
            };

            ListItem::new(format!(
                "GPU {}: {} ({}/{} MB)\n{}",
                gpu.index, gpu.name, gpu.memory_used, gpu.memory_total, job_info
            ))
        })
        .collect();

    let list = List::new(items)
        .block(Block::default().borders(Borders::ALL).title("GPUs"))
        .highlight_style(Style::default().bg(Color::DarkGray));

    let mut list_state = ListState::default();
    list_state.select(Some(state.list_state.selected().unwrap_or(0)));
    frame.render_stateful_widget(list, area, &mut list_state);
}

// Update queue view to use ListState
fn draw_queue_view(state: &AppState, frame: &mut ratatui::Frame, area: Rect) {
    let items: Vec<ListItem> = state
        .jobs
        .iter()
        .filter(|j| matches!(j.status, JobStatus::Queued))
        .map(|job| ListItem::new(Text::from(job.command.clone())))
        .collect();

    let list = List::new(items)
        .block(Block::default().borders(Borders::ALL).title("Queue"))
        .highlight_style(Style::default().bg(Color::DarkGray));

    let mut list_state = ListState::default();
    list_state.select(Some(state.list_state.selected().unwrap_or(0)));
    frame.render_stateful_widget(list, area, &mut list_state);
}

// Update history view to use ListState
fn draw_history_view(state: &AppState, frame: &mut ratatui::Frame, area: Rect) {
    let items: Vec<ListItem> = state
        .jobs
        .iter()
        .filter(|j| matches!(j.status, JobStatus::Completed))
        .map(|job| {
            let runtime = job
                .start_time
                .map(|t| t.elapsed().unwrap_or_default())
                .unwrap_or_default();
            ListItem::new(Text::from(format!(
                "{}\nRuntime: {}m {}s | GPU: {}",
                job.command,
                runtime.as_secs() / 60,
                runtime.as_secs() % 60,
                job.gpu_index.unwrap_or(0)
            )))
        })
        .collect();

    let list = List::new(items)
        .block(Block::default().borders(Borders::ALL).title("History"))
        .highlight_style(Style::default().bg(Color::DarkGray));

    let mut list_state = ListState::default();
    list_state.select(Some(state.list_state.selected().unwrap_or(0)));
    frame.render_stateful_widget(list, area, &mut list_state);
}

// Event handling
fn handle_input(state: &mut AppState) -> io::Result<bool> {
    if state.is_command_mode {
        return handle_command_mode(state);
    }

    if event::poll(Duration::from_millis(100))? {
        if let Event::Key(key) = event::read()? {
            match key.code {
                KeyCode::Char('v') => {
                    let editor = std::env::var("EDITOR").unwrap_or_else(|_| "vim".to_string());
                    Command::new(editor)
                        .arg(state.base_path.join("jobs.txt"))
                        .status()?;
                    state.jobs = load_jobs_from_file(&state.base_path.join("jobs.txt"))?;
                }
                KeyCode::Char(' ') => {
                    state.is_paused = !state.is_paused;
                }
                KeyCode::Enter => match state.current_view {
                    View::Home => {
                        if let Some(selected) = state.list_state.selected() {
                            if let Some(job) = state.jobs.iter().find(|j| {
                                matches!(j.status, JobStatus::Running)
                                    && j.gpu_index == Some(selected)
                            }) {
                                if let Some(session) = &job.screen_session {
                                    Command::new("screen").args(["-r", session]).status()?;
                                }
                            }
                        }
                    }
                    View::History => {
                        if let Some(selected) = state.list_state.selected() {
                            if let Some(job) = state.jobs.iter().find(|j| {
                                matches!(j.status, JobStatus::Completed)
                                    && state.jobs.iter().position(|x| x.command == j.command)
                                        == Some(selected)
                            }) {
                                if let Some(log_dir) = &job.log_dir {
                                    state.show_logs = Some(log_dir.clone());
                                }
                            }
                        }
                    }
                    _ => {}
                },
                _ => {}
            }
        }
    }
    Ok(false)
}

pub fn run_app(mut state: AppState) -> io::Result<()> {
    // Setup terminal
    crossterm::terminal::enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)?;
    let mut terminal = Terminal::new(CrosstermBackend::new(stdout))?;

    loop {
        terminal.draw(|f| {
            let chunks = Layout::default()
                .direction(Direction::Vertical)
                .constraints([
                    Constraint::Length(1), // Status line
                    Constraint::Min(1),    // Main content
                    Constraint::Length(1), // Command input or controls
                ])
                .split(f.size());
            // Draw main UI
            draw_ui(&state, f);
            // Draw command mode or logs
            if state.is_command_mode {
                draw_command_mode(&state, f, chunks[2]);
            } else if let Some(log_path) = &state.show_logs {
                if let Err(e) = draw_log_view(log_path, f, chunks[1]) {
                    state.error_message = Some(format!("Error reading logs: {}", e));
                }
            }
            // Draw error message if any
            if let Some(error) = &state.error_message {
                let error_msg = Paragraph::new(error.as_str())
                    .style(Style::default().fg(Color::Red))
                    .block(Block::default().borders(Borders::ALL));
                let error_area = Rect {
                    x: chunks[1].x + 2,
                    y: chunks[1].y + 2,
                    width: chunks[1].width.saturating_sub(4),
                    height: 3,
                };
                f.render_widget(error_msg, error_area);
            }
        })?;

        // Handle input
        if let Event::Key(key) = event::read()? {
            if handle_input(&mut state)? {
                break;
            }
        }

        // Check for completed jobs
        check_completed_jobs(&mut state)?;

        // Update GPU info periodically (every 5 seconds)
        if let Ok(elapsed) = state.last_gpu_check.elapsed() {
            if elapsed.as_secs() >= 5 {
                if let Ok(info) = get_gpu_info() {
                    state.gpu_info = info;
                    state.last_gpu_check = SystemTime::now();
                }
            }
        }

        // Process queued jobs if not paused
        if !state.is_paused {
            if let Err(e) = process_queue(&mut state) {
                state.error_message = Some(format!("Queue processing error: {}", e));
            }
        }

        // Auto-save jobs periodically
        save_jobs_to_file(&state.jobs, &state.config.jobs_file)?;
    }

    // Cleanup
    crossterm::terminal::disable_raw_mode()?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)?;
    Ok(())
}

fn process_queue(state: &mut AppState) -> io::Result<()> {
    let available_gpus: Vec<usize> = state
        .gpu_info
        .iter()
        .enumerate()
        .filter(|(i, _)| {
            !state
                .jobs
                .iter()
                .any(|j| matches!(j.status, JobStatus::Running) && j.gpu_index == Some(*i))
        })
        .map(|(i, _)| i)
        .collect();

    for gpu_index in available_gpus {
        if let Some(job) = state
            .jobs
            .iter_mut()
            .find(|j| matches!(j.status, JobStatus::Queued))
        {
            start_job(job, gpu_index, &state.base_path)?;
        }
    }

    Ok(())
}

// Command History Management
fn create_command_history() -> CommandHistory {
    CommandHistory {
        entries: VecDeque::with_capacity(100),
        position: None,
        max_entries: 100,
    }
}

fn add_to_history(history: &mut CommandHistory, command: &str) {
    if !command.trim().is_empty() {
        if history.entries.contains(&command.to_string()) {
            history.entries.retain(|x| x != command);
        }
        history.entries.push_front(command.to_string());
        if history.entries.len() > history.max_entries {
            history.entries.pop_back();
        }
    }
}

// File Management
fn create_log_directory(job: &Job, base_path: &Path) -> io::Result<PathBuf> {
    let timestamp = SystemTime::now()
        .duration_since(SystemTime::UNIX_EPOCH)
        .unwrap()
        .as_secs();
    let log_dir = base_path
        .join("logs")
        .join(format!("job_{}_{}", timestamp, job.command_hash));
    fs::create_dir_all(&log_dir)?;
    Ok(log_dir)
}

fn save_jobs_to_file(jobs: &[Job], path: &Path) -> io::Result<()> {
    let mut file = OpenOptions::new()
        .write(true)
        .truncate(true)
        .create(true)
        .open(path)?;

    for job in jobs
        .iter()
        .filter(|j| matches!(j.status, JobStatus::Queued))
    {
        writeln!(file, "{}", job.command)?;
    }
    Ok(())
}

fn load_jobs_from_file(path: &Path) -> io::Result<Vec<Job>> {
    let file = File::open(path)?;
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

// UI Components
fn draw_command_mode(state: &AppState, frame: &mut ratatui::Frame, area: Rect) {
    let prompt = "Add job > ";
    let input = format!("{}{}", prompt, state.command_input);
    let paragraph = Paragraph::new(input)
        .style(Style::default().fg(Color::Yellow))
        .block(Block::default().borders(Borders::ALL));
    frame.render_widget(paragraph, area);
}

fn draw_log_view(path: &Path, frame: &mut ratatui::Frame, area: Rect) -> io::Result<()> {
    let mut content = String::new();

    // Read stdout
    if let Ok(mut file) = File::open(path.join("stdout.log")) {
        file.read_to_string(&mut content)?;
    }
    content.push_str("\n\n=== STDERR ===\n\n");

    // Read stderr
    if let Ok(mut file) = File::open(path.join("stderr.log")) {
        file.read_to_string(&mut content)?;
    }

    let paragraph = Paragraph::new(Text::raw(content))
        .block(Block::default().borders(Borders::ALL).title("Job Logs"))
        .wrap(Wrap { trim: true });
    frame.render_widget(paragraph, area);

    Ok(())
}

// Enhanced Event Handling
fn handle_command_mode(state: &mut AppState) -> io::Result<bool> {
    if let Event::Key(key) = event::read()? {
        match key.code {
            KeyCode::Esc => {
                state.is_command_mode = false;
                state.command_input.clear();
                state.command_history.position = None;
            }
            KeyCode::Enter => {
                let command = state.command_input.trim().to_string();
                if !command.is_empty() {
                    add_to_history(&mut state.command_history, &command);
                    state.jobs.push(create_job(command));
                    save_jobs_to_file(&state.jobs, &state.base_path.join("jobs.txt"))?;
                }
                state.is_command_mode = false;
                state.command_input.clear();
                state.command_history.position = None;
            }
            KeyCode::Char(c) => {
                state.command_input.push(c);
            }
            KeyCode::Backspace => {
                state.command_input.pop();
            }
            KeyCode::Up => {
                if let Some(pos) = state.command_history.position {
                    if pos + 1 < state.command_history.entries.len() {
                        state.command_history.position = Some(pos + 1);
                        state.command_input = state.command_history.entries[pos + 1].clone();
                    }
                } else if !state.command_history.entries.is_empty() {
                    state.command_history.position = Some(0);
                    state.command_input = state.command_history.entries[0].clone();
                }
            }
            KeyCode::Down => {
                if let Some(pos) = state.command_history.position {
                    if pos > 0 {
                        state.command_history.position = Some(pos - 1);
                        state.command_input = state.command_history.entries[pos - 1].clone();
                    } else {
                        state.command_history.position = None;
                        state.command_input.clear();
                    }
                }
            }
            _ => {}
        }
    }
    Ok(false)
}

// Main application initialization and loop

// Update init_app to use ListState and Config
fn init_app() -> io::Result<AppState> {
    let home = dirs::home_dir()
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "Could not find home directory"))?;
    let base_path = home.join(".nexus");
    fs::create_dir_all(&base_path)?;
    fs::create_dir_all(base_path.join("logs"))?;

    let config = Config {
        log_dir: base_path.join("logs"),
        jobs_file: base_path.join("jobs.txt"),
    };

    if !config.jobs_file.exists() {
        File::create(&config.jobs_file)?;
    }

    let mut list_state = ListState::default();
    list_state.select(Some(0));

    Ok(AppState {
        jobs: load_jobs_from_file(&config.jobs_file)?,
        gpu_info: get_gpu_info()?,
        current_view: View::Home,
        list_state,
        is_paused: false,
        command_input: String::new(),
        is_command_mode: false,
        command_history: create_command_history(),
        show_logs: None,
        base_path,
        error_message: None,
        last_gpu_check: SystemTime::now(),
        config,
    })
}

fn check_completed_jobs(state: &mut AppState) -> io::Result<()> {
    for job in state.jobs.iter_mut() {
        if matches!(job.status, JobStatus::Running) {
            if let Some(session) = &job.screen_session {
                // Check if screen session exists
                let output = Command::new("screen").args(["-ls", session]).output()?;

                if !String::from_utf8_lossy(&output.stdout).contains(&format!(".{}", session)) {
                    job.status = JobStatus::Completed;

                    // Archive logs if they exist
                    if let Some(log_dir) = &job.log_dir {
                        if log_dir.exists() {
                            let archive_dir = state.base_path.join("logs").join("archived");
                            fs::create_dir_all(&archive_dir)?;

                            let timestamp = SystemTime::now()
                                .duration_since(SystemTime::UNIX_EPOCH)
                                .unwrap()
                                .as_secs();

                            let archive_path = archive_dir
                                .join(format!("job_{}_{}.tar.gz", timestamp, job.command_hash));

                            // Create tar archive of logs
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
        }
    }
    Ok(())
}

// Configuration file handling
fn load_config(base_path: &Path) -> io::Result<toml::Value> {
    let config_path = base_path.join("config.toml");
    if !config_path.exists() {
        let default_config = format!(
            r#"[paths]
log_dir = "{}"
jobs_file = "{}"
"#,
            base_path.join("logs").display(),
            base_path.join("jobs.txt").display()
        );
        fs::write(&config_path, default_config)?;
    }

    let config_str = fs::read_to_string(&config_path)?;
    toml::from_str(&config_str).map_err(|e| {
        io::Error::new(
            io::ErrorKind::InvalidData,
            format!("Failed to parse config.toml: {}", e),
        )
    })
}

// Update the elapsed() error handling
fn check_gpu_update(state: &mut AppState) -> io::Result<()> {
    if let Ok(elapsed) = state.last_gpu_check.elapsed() {
        if elapsed.as_secs() >= 5 {
            if let Ok(info) = get_gpu_info() {
                state.gpu_info = info;
                state.last_gpu_check = SystemTime::now();
            }
        }
    }
    Ok(())
}

fn main() -> io::Result<()> {
    // Check if running in screen session
    let screen_session = std::env::var("STY").unwrap_or_default();
    if screen_session.is_empty() {
        // Start new screen session
        Command::new("screen")
            .args(["-dmS", "nexus", std::env::current_exe()?.to_str().unwrap()])
            .output()?;
        println!("Started nexus in screen session. Attach with: screen -r nexus");
        return Ok(());
    }

    // Set up panic handler
    std::panic::set_hook(Box::new(|panic_info| {
        let _ = crossterm::terminal::disable_raw_mode();
        let mut stdout = io::stdout();
        let _ = execute!(stdout, LeaveAlternateScreen);
        eprintln!("Nexus crashed: {}", panic_info);
    }));

    // Initialize the application state
    let app_state = init_app()?;

    // Run the application
    if let Err(e) = run_app(app_state) {
        eprintln!("Application error: {}", e);
        std::process::exit(1);
    }

    Ok(())
}

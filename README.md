# 🖥️ LLM Hardware Monitor

A daily monitoring daemon that tracks hardware availability, LLM model releases, and deals — helping you find the best local LLM setup for 24/7 autonomous coding agents.

Built with [GitHub Copilot CLI](https://github.com/features/copilot) as the research engine and BurntToast for Windows notifications.

## What It Tracks

| Category | Items |
|---|---|
| **Hardware** | Mac Studio M5 announcements, M4 Max 128GB stock (India/US), Apple Refurbished, WWDC dates, Corsair WS300, AMD Strix Halo 128GB |
| **Models & Agents** | New MoE models, coding models, MLX/llama.cpp updates, YOLO coding agent frameworks |
| **Deals & News** | Apple India deals, marketplace prices, r/LocalLLaMA + HN news, trending HuggingFace models |
| **Daily Recommendation** | Buy now / wait / consider alternative — with reasoning, model config, fine-tuning guide, and buy links |

## Features

- **Timeline Dashboard** — Grouped by date, incremental (only shows changes), searchable with category filters
- **Side-Pane Modal** — Click any card for detailed analysis, enrichment data, and a reference links table
- **Daily Recommendation Card** — AI-generated buy/wait advice based on all current data + your constraints
- **Windows Toast Notifications** — With severity levels, app icon, click-to-open dashboard
- **Portable State** — `monitor_state.json` is git-tracked; clone on a new machine and continue from where you left off
- **Enrichment Prompts** — Deeper analysis for model updates (benchmarks, tok/s, quantization details)

## User Constraints (configured in code)

- Budget: ₹1.5–3.5 lakh (flexible)
- Location: India (can buy from USA/Canada)
- Need: 128GB unified memory for 30B+ parameter models at 25+ tok/s
- Use case: 24/7 YOLO coding agents with good reasoning
- Open to: Apple Silicon, AMD Strix Halo
- Also wants: local fine-tuning capability

## Setup

### Prerequisites

- **Windows 10/11** with PowerShell 7+
- **Python 3.10+** (no pip dependencies — stdlib only)
- **GitHub Copilot CLI** installed and authenticated (`copilot login`)
- **BurntToast** PowerShell module for toast notifications:
  ```powershell
  Install-Module -Name BurntToast -Scope CurrentUser
  ```

### Quick Start

```bash
# Clone the repo
git clone <repo-url> C:\Work\Personal\llm-hardware-monitor
cd C:\Work\Personal\llm-hardware-monitor

# Run manually
python monitor.py

# Open the dashboard
start LLM-Hardware-Monitor.html
```

### Schedule Daily Runs

**Option A — PowerShell (recommended):**
```powershell
$dir = "C:\Work\Personal\llm-hardware-monitor"
$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c cd /d `"$dir`" && python monitor.py" -WorkingDirectory $dir
$trigger = New-ScheduledTaskTrigger -Daily -At 9:00AM
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
Register-ScheduledTask -TaskName "LLM-Hardware-Monitor" -Action $action -Trigger $trigger -Settings $settings
```

**Option B — Batch file:**
```bash
# Run as Administrator
setup-task.bat
```

### Run on Another Machine

1. Clone this repo (state file carries over your full timeline history)
2. Install prerequisites (Python, Copilot CLI, BurntToast)
3. Run `python monitor.py` or register the scheduled task
4. It picks up from the last tracked state automatically

## Project Structure

```
llm-hardware-monitor/
├── monitor.py                 # Main daemon (~1500 lines)
├── monitor_state.json         # Persistent state (git-tracked, portable)
├── LLM-Hardware-Monitor.html  # Dashboard output (git-tracked)
├── icon.png                   # Toast notification icon
├── run-now.bat                # Quick manual run helper
├── setup-task.bat             # Task Scheduler registration
├── .gitignore
└── README.md
```

## How It Works

```
┌─────────────┐     ┌──────────────┐     ┌────────────────┐
│  Scheduled   │────▶│  monitor.py  │────▶│  Copilot CLI   │
│  Task (9AM)  │     │              │     │  (web search)  │
└─────────────┘     │  3 category  │     └────────────────┘
                    │  prompts     │
                    │  2 enrichment│     ┌────────────────┐
                    │  1 recommend │────▶│  Dashboard HTML │
                    │              │     │  (timeline +   │
                    │  Change      │     │   side modals)  │
                    │  detection   │     └────────────────┘
                    │              │
                    │  State mgmt  │     ┌────────────────┐
                    │              │────▶│  Toast Notif    │
                    └──────────────┘     │  (BurntToast)  │
                                        └────────────────┘
```

Each run makes **6 Copilot CLI calls** (3 categories + 2 enrichment + 1 recommendation), costing ~6 premium requests per day.

## Copilot CLI Integration Notes

- Uses `-p` (prompt) mode with `--yolo -s --effort low` flags
- `.cmd` wrapper path required for Python `subprocess` on Windows
- Shell metacharacters (`|`, `&`, `<`, `>`) are escaped with `^` for `cmd.exe` compatibility
- JSON parsing handles Copilot's tool progress indicators in text mode output
- Prompts are kept compact (<6000 chars) to stay within Windows command line limits

## License

MIT

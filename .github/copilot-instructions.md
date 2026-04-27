# Copilot Instructions — LLM Hardware Monitor

## Project Context
This is a daily monitoring daemon that uses GitHub Copilot CLI (`copilot -p`) to track
hardware availability, LLM model releases, and deals for building a local LLM PC in India.

## User Constraints
- Budget: ₹1.5–3.5 lakh (flexible)
- Location: India (can buy from USA/Canada)
- Need: 128GB unified memory, 30B+ models at 25+ tok/s
- Use case: 24/7 YOLO coding agents with reasoning
- Targets: Apple Silicon (Mac Studio M5 Max) or AMD Strix Halo
- Also wants: local fine-tuning capability

## Key Architecture Decisions
- **Copilot CLI as research engine**: Uses `-p` prompt mode with `--yolo -s --effort low`
- **`.cmd` wrapper**: Must use `C:\ProgramData\global-npm\copilot.cmd` for subprocess on Windows
- **Shell escaping**: `|`, `&`, `<`, `>` must be `^`-escaped for cmd.exe
- **JSON parsing**: Finds largest JSON object in text output (Copilot mixes tool progress with response)
- **Prompt length limit**: Must stay under ~6000 chars (Windows cmd line limit is ~8191)
- **State is git-tracked**: `monitor_state.json` carries timeline, enrichment, recommendations across machines

## Files
- `monitor.py` — Main daemon (prompts, copilot runner, JSON parser, change detection, toast, dashboard, recommendation)
- `monitor_state.json` — Persistent state (tracked in git for portability)
- `LLM-Hardware-Monitor.html` — Dashboard output (tracked in git)
- `icon.png` — Toast notification icon (64x64 blue-purple circle)

# Copilot Instructions — LLM Hardware Monitor

## Project Context
This is a self-evolving daily monitoring daemon that uses GitHub Copilot CLI (`copilot -p`) to track
hardware availability, LLM model releases, and deals for building a local LLM machine.

## User Goal
**24/7 YOLO coding agents** using local LLMs with good reasoning, context window, and tokens/second.
- **Inference**: 30B-70B models at 25+ tok/s (MoE models like Qwen3-30B-A3B are game-changers)
- **Fine-tuning**: 7B-14B coding models locally
- **Budget**: ₹1.5-5L / $1,500-5,000 (flexible for exceptional value)
- **Buy from**: India, USA, or Canada
- **Minimum**: 48GB unified/shared memory OR 24GB discrete GPU VRAM

## Current Decision
**Waiting for Mac Studio M5 Max 128GB** (WWDC June 2026, ship ~Oct 2026).

## Prior Research Conclusions (DO NOT re-investigate)
- RTX 4090: DISCONTINUED Oct 2024 — not available new
- RTX 4070 Ti Super / RTX 5080: 16GB VRAM too small for 30B+ models
- Dual GPU: unreliable, Ollama no support, llama.cpp 1.5x scaling only
- Mac Studio M4 Max 128GB India: ₹3.45-3.65L, was out of stock globally (DRAM shortage)
- Mac Mini M4 Pro 48GB India: ₹1.86-2.04L, orderable, runs 32B+128k
- Bosgame M5 128GB Strix Halo: $1,699-2,599, 66-72 tok/s MoE, no India warranty
- Corsair AI WS 300: $2,699 — same price as Mac Studio but 2.3x slower
- Cloud costs: Sonnet 4.6 24/7 = ₹1.32 Cr/year vs local ₹7,200/year
- Quality gap: Sonnet 4.6 ~80% SWE-bench vs Qwen3-30B MoE ~45% hard tasks
- Hybrid approach: local 70% routine + cloud 30% hard tasks
- Apple warranty works globally in India
- Exchange rate: 1 USD = ₹94.13

## Key Architecture Decisions
- **Copilot CLI as research engine**: Uses `-p` prompt mode with `--yolo -s --effort low`
- **`.cmd` wrapper**: Must use `C:\ProgramData\global-npm\copilot.cmd` for subprocess on Windows
- **Shell escaping**: `|`, `&`, `<`, `>` must be `^`-escaped for cmd.exe
- **JSON parsing**: Finds largest JSON object in text output (Copilot mixes tool progress with response)
- **Prompt length limit**: Must stay under ~6000 chars (Windows cmd line limit is ~8191)
- **State is git-tracked**: `monitor_state.json` carries timeline, enrichment, recommendations across machines
- **Dynamic discovery**: New hardware extracted from monitoring results, validated via Playwright, promoted after 2+ successful checks
- **Goal-anchored**: All prompts include user's specific goal; discoveries filtered by RELEVANCE_KEYWORDS

## Files
- `monitor.py` — Main daemon (prompts, copilot runner, JSON parser, change detection, toast, dashboard, recommendation, dynamic discovery)
- `monitor_state.json` — Persistent state including dynamic stores (tracked in git for portability)
- `LLM-Hardware-Monitor.html` — Dashboard output (tracked in git)
- `icon.png` — Toast notification icon (64x64 blue-purple circle)

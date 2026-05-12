#!/usr/bin/env python3
"""
LLM Hardware Monitor Daemon
============================
Daily monitoring script that uses GitHub Copilot CLI to check:
- Mac Studio M5 announcements & availability
- AMD Strix Halo alternatives in India
- LLM model breakthroughs (MoE, coding models)
- Deals, pricing changes
- Latest blogs/news from r/LocalLLaMA, HN, etc.
- YOLO coding agent framework updates

Outputs:
- Windows toast notification (via BurntToast PowerShell module)
- HTML dashboard in project folder
- Log file

Scheduled via Windows Task Scheduler to run daily at 9 AM.
"""

import json
import subprocess
import os
import sys
import re
import hashlib
import logging
import asyncio
import urllib.request
import urllib.error
import ssl
import time
from datetime import datetime, timedelta
from pathlib import Path
from logging.handlers import RotatingFileHandler

# Optional: Playwright for direct Apple Store scraping
try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

# ─── Configuration ───────────────────────────────────────────────────────────

MONITOR_DIR = Path(__file__).parent
STATE_FILE = MONITOR_DIR / "monitor_state.json"
LOG_FILE = MONITOR_DIR / "monitor.log"
DASHBOARD_FILE = MONITOR_DIR / "LLM-Hardware-Monitor.html"
INDEX_FILE = MONITOR_DIR / "index.html"
PAGES_DIR = MONITOR_DIR / "pages"

# Call node directly with npm-loader.js to bypass .cmd metacharacter issues
COPILOT_CMD = "node"
COPILOT_SCRIPT = r"C:\ProgramData\global-npm\node_modules\@github\copilot\npm-loader.js"

# Per-category session naming for "Discuss in CLI" resume
import secrets
_RUN_ID = secrets.token_hex(2)  # 4-char hex, unique per run
_RUN_DATE = datetime.now().strftime('%Y-%m-%d')

def _session_name_for(category: str) -> str:
    """Generate a unique session name for a given prompt category."""
    return f"llm-monitor-{category}-{_RUN_DATE}-{_RUN_ID}"

# Copilot invocation flags — use text mode (more reliable than json for subprocess)
COPILOT_FLAGS = [
    "--yolo",                    # no permission prompts
    "-s",                        # silent (no stats banner)
    "--effort", "low",           # save tokens
    "--disable-mcp-server", "DRIAssistMCP.Local.Test",  # skip unneeded MCP
    "--no-custom-instructions",  # skip AGENTS.md etc
]

# ─── Logging ─────────────────────────────────────────────────────────────────

logger = logging.getLogger("llm-monitor")
logger.setLevel(logging.INFO)
handler = RotatingFileHandler(LOG_FILE, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(handler)
# Console handler with safe encoding
import io
console = logging.StreamHandler(
    stream=io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
)
console.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(console)

# ─── Monitoring Prompts (by category) ────────────────────────────────────────

PROMPTS = {
    "hardware": (
        "You are a hardware monitoring agent. Today is {date}. "
        "GOAL: Find the best machine for 24/7 local LLM coding agents — "
        "30B-70B model inference at 25+ tok/s AND 7B-14B fine-tuning. "
        "Needs unified memory >=48GB or GPU VRAM >=24GB. Budget INR 1.5-5L / USD 1500-5000. "
        "Search the web and return ONLY a JSON object (no markdown fences, no explanation). "
        "Check each of these items and return exactly this structure: "
        '{{"mac_studio_m5": {{"announced": false, "info": "summary of Mac Studio M5 rumors/announcements"}}, '
        '"mac_studio_128gb_india": {{"in_stock": false, "orderable": false, "delivery_days": "unknown", "price_inr": "unknown", '
        '"info": "Check apple.com/in/shop/buy-mac/mac-studio for Mac Studio M4 Max 16-core CPU 40-core GPU 128GB. Is it orderable?"}}, '
        '"mac_studio_128gb_us": {{"in_stock": false, "orderable": false, "delivery_days": "unknown", "price_usd": "unknown", '
        '"info": "Check apple.com/shop/buy-mac/mac-studio for Mac Studio M4 Max 128GB in US store"}}, '
        '"mac_mini_48gb_india": {{"in_stock": false, "orderable": false, "price_inr": "unknown", '
        '"info": "Mac Mini M4 Pro 48GB on apple.com/in — orderable? Price?"}}, '
        '"mac_mini_48gb_us": {{"in_stock": false, "orderable": false, "price_usd": "unknown", '
        '"info": "Mac Mini M4 Pro 48GB on apple.com — orderable? Price?"}}, '
        '"apple_refurbished": {{"available": false, "info": "summary"}}, '
        '"wwdc_apple_event": {{"date": "TBD", "info": "summary"}}, '
        '"framework_desktop_128gb": {{"available": false, "price_usd": "unknown", "info": "Framework Desktop Strix Halo 128GB — orderable on frame.work? ETA?"}}, '
        '"bosgame_m5_128gb": {{"available": false, "price_usd": "unknown", "info": "Bosgame M5 Strix Halo 128GB — in stock? Price?"}}, '
        '"beelink_gtr9_pro_128gb": {{"available": false, "price_usd": "unknown", "info": "Beelink GTR9 Pro Strix Halo 128GB — shipping? Price?"}}, '
        '"minisforum_ms_s1_max": {{"available": false, "price_usd": "unknown", "info": "Minisforum MS-S1 Max Strix Halo 128GB — orderable? Price?"}}, '
        '"corsair_ws300": {{"available": false, "price_usd": "unknown", "info": "Corsair AI Workstation 300 Strix Halo 128GB — orderable? Price?"}}, '
        '"amd_strix_halo_128gb_india": {{"available": false, "info": "ASUS ProArt PX13 Strix Halo 128GB in India — available? Price?"}}, '
        '"rtx_5090_india": {{"available": false, "price_inr": "unknown", "info": "RTX 5090 FE or AIB in India — Amazon/Flipkart availability and price?"}}, '
        '"rtx_5090_us": {{"available": false, "price_usd": "unknown", "info": "RTX 5090 availability in US — any store?"}}, '
        '"new_hardware_discoveries": {{"found": false, "items": [], '
        '"info": "Search blogs, r/LocalLLaMA, Hacker News for ANY new mini PC, workstation, or GPU with >=48GB unified memory or >=24GB VRAM under $5000 '
        'that we are NOT already tracking above. Include product name, memory, price, store URL. '
        'Exclude: RTX 4090 (discontinued), RTX 4070/5080 (16GB too small), dual-GPU setups, OLX listings."}}}} '
        "For Mac configs: report orderable=true only if add-to-bag works. Include delivery estimate and price. "
        "For new_hardware_discoveries.items: array of {{\"name\": \"product\", \"memory_gb\": N, \"price\": \"$X\", \"url\": \"store_link\"}}. "
        "Return ONLY the JSON."
    ),

    "models_and_agents": (
        "You are an AI model monitoring agent. Today is {date}. "
        "GOAL: Find the best models and tools for 24/7 LOCAL autonomous coding agents. "
        "Target hardware: 48-128GB unified memory (Apple Silicon/Strix Halo) or 24-32GB VRAM (RTX 5090). "
        "Need: models good at coding (30B-70B inference) + smaller models for fine-tuning (7B-14B). "
        "Search the web and return ONLY a JSON object (no markdown fences, no explanation). "
        "Check each of these 4 items and return exactly this structure: "
        '{{"best_local_coding_models": {{"found": false, "info": "Best models released in last 30 days for LOCAL coding tasks — any architecture (dense, MoE, hybrid). '
        "Must run on 48-128GB unified memory or 24-32GB VRAM. Compare on: HumanEval/SWE-bench scores, tok/s on target hardware, quantization options. "
        'Current baseline: Qwen3-30B-A3B ~160 tok/s on M4 Max 128GB. Report anything that beats this for coding."}}, '
        '"fine_tuning_models": {{"found": false, "info": "Best 7B-14B models for fine-tuning on coding tasks that run on 48-128GB RAM. '
        'New releases, LoRA/QLoRA support, training frameworks. Any breakthroughs in efficient fine-tuning?"}}, '
        '"inference_runtimes": {{"info": "Latest updates to ANY local inference runtime or engine — MLX, llama.cpp, vLLM, Ollama, exllamav2, TensorRT-LLM, etc. '
        'Focus on: Apple Silicon optimizations, AMD iGPU (Strix Halo ROCm/Vulkan) support, speed improvements, new model support."}}, '
        '"coding_agent_frameworks": {{"info": "Latest autonomous coding agent frameworks and tools — ANY framework that supports local models for YOLO/unattended coding. '
        'New releases, major updates, local-model performance comparisons. Which frameworks work best with 30B-70B local models?"}}}}}} '
        "Replace each info with real current findings. Return ONLY the JSON."
    ),

    "deals_and_blogs": (
        "You are a deals and tech news monitoring agent. Today is {date}. "
        "GOAL: Track deals and news for local LLM hardware. Budget INR 1.5-5L / USD 1500-5000. "
        "Focus: Mac Studio/Mini, Strix Halo mini PCs, RTX 5090/5080 GPUs. "
        "Search the web and return ONLY a JSON object (no markdown fences, no explanation). "
        "Check each of these items and return exactly this structure: "
        '{{"apple_india_deals": {{"has_deals": false, "info": "Mac Studio/Mac Mini deals, education discount, card offers on apple.com/in"}}, '
        '"strix_halo_deals": {{"has_deals": false, "info": "deals or price drops on Strix Halo mini PCs (Framework, Bosgame, Beelink, Minisforum, Corsair) for LLM use"}}, '
        '"gpu_deals_india": {{"has_deals": false, "info": "RTX 5090/5080 deals in India — any card under INR 3.5L? Include store links"}}, '
        '"mac_studio_marketplace": {{"info": "Mac Studio 128GB availability and prices on Amazon India, Flipkart, refurbished stores"}}, '
        '"latest_local_llm_news": {{"info": "top 3 developments from r/LocalLLaMA, HN about running 30B-70B models locally or fine-tuning 7B-14B models"}}, '
        '"trending_models": {{"info": "top 3 trending models on HuggingFace for local coding agents — must run on 48-128GB unified memory or 24-32GB VRAM"}}, '
        '"new_products_from_blogs": {{"found": false, '
        '"info": "Any NEW products announced in tech blogs/reviews in the last 2 weeks that have >=48GB unified memory or >=24GB VRAM and cost <$5000. '
        'Include product name, specs, price, and source URL. Exclude discontinued items (RTX 4090) and 16GB VRAM cards."}}}} '
        "Include store URLs when available. Replace each info with real current findings. Return ONLY the JSON."
    ),
    "efficiency_research": (
        "You are an LLM efficiency researcher. Today is {date}. "
        "Search the web and return ONLY a JSON object (no markdown fences, no explanation). "
        "Focus on breakthroughs in running large language models on budget/lower-end hardware. "
        "Signal levels: \"breakthrough\" = 2x+ speed improvement, >30% VRAM reduction, or enables previously impossible configs (e.g., 70B on 16GB GPU); "
        "\"notable\" = 10-50% improvements, new tools/models worth tracking; "
        "\"noise\" = minor version bumps, <10% improvements. "
        "Check each of these items and return exactly this structure: "
        '{{"quantization_breakthroughs": {{"found": false, "signal": "noise", '
        '"info": "New quantization methods reducing VRAM — GPTQ, AWQ, GGUF improvements, new formats. Any method enabling 30B+ models on 8-16GB VRAM?"}}, '
        '"inference_engine_updates": {{"found": false, "signal": "noise", '
        '"info": "Updates to llama.cpp, vLLM, KTransformers, PowerInfer, SGLang, exllamav2, TensorRT-LLM — speed gains, new features, new hardware support"}}, '
        '"moe_offloading": {{"found": false, "signal": "noise", '
        '"info": "MoE expert offloading improvements — --n-cpu-moe in llama.cpp, heterogeneous CPU/GPU placement, partial offloading strategies"}}, '
        '"budget_gpu_benchmarks": {{"found": false, "signal": "noise", '
        '"info": "Budget GPU benchmarks for LLMs — RTX 4060 Ti 16GB, RTX 3060 12GB, GTX 1060 running 30B+ models. tok/s numbers, configs used"}}, '
        '"efficient_model_architectures": {{"found": false, "signal": "noise", '
        '"info": "New efficient model architectures — MoE with few active params like Qwen3-30B-A3B, sparse models, distilled models that punch above their weight"}}, '
        '"memory_optimization": {{"found": false, "signal": "noise", '
        '"info": "Memory optimization tricks — KV cache compression, speculative decoding advances, flash attention improvements, paged attention updates"}}, '
        '"community_discoveries": {{"found": false, "signal": "noise", '
        '"info": "Reddit r/LocalLLaMA discoveries, YouTube demos, GitHub PRs showing big models on small GPUs. Practical configs people are actually using"}}}} '
        "Replace each info with real current findings. Return ONLY the JSON."
    ),
    "learning_feed": (
        "You are a local LLM learning curator. Today is {date}. "
        "Search Reddit r/LocalLLaMA, YouTube, Hacker News, and tech blogs for the most valuable and educational content from the past 7 days. "
        "Focus topics: running LLMs locally, fine-tuning techniques, coding agent frameworks, hardware optimization, quantization advances, new model releases for local use. "
        "Only include content with genuine educational value. Skip news-only items, product announcements without technical depth, and clickbait. "
        "Return ONLY a JSON object (no markdown fences, no explanation) with 5-10 items sorted by relevance: "
        '{{"articles": ['
        '{{"title": "article title", "url": "real URL", "source": "reddit|youtube|hackernews|blog|github", '
        '"category": "inference|training|agents|hardware|quantization|models", '
        '"summary": "1-2 sentence summary of key insight", '
        '"relevance": "high|medium", '
        '"type": "article|video|tutorial|discussion|paper"}}'
        "]}} "
        "Replace placeholders with real current findings. Return ONLY the JSON."
    ),
    "model_benchmarks": (
        "You are an LLM benchmarking analyst. Today is {date}. "
        "Search the web and return ONLY a JSON object (no markdown fences, no explanation). "
        "Find the top 5 coding-capable models ranked by coding capability that can run locally. "
        "Include: Qwen3 variants, DeepSeek variants, Llama variants, Codestral, and any strong new entrant. "
        "Models must fit on 48-128GB unified memory OR 16-32GB VRAM with quantization. "
        "Return exactly this structure: "
        '{{"top_coding_models": ['
        '{{"name": "model name (e.g., Qwen3-30B-A3B)", '
        '"params": "30B", '
        '"architecture": "MoE|dense|hybrid", '
        '"active_params": "3B (for MoE, null for dense)", '
        '"humaneval_plus": 72.5, '
        '"swe_bench_verified": 45.2, '
        '"livecode_bench": null, '
        '"vram_q4": "20GB", '
        '"vram_q8": "34GB", '
        '"tok_s_128gb_unified": 160, '
        '"tok_s_48gb_unified": 45, '
        '"tok_s_rtx5090": 80, '
        '"tok_s_rtx4060ti_16gb_moe_offload": 35, '
        '"best_quant": "Q4_K_M", '
        '"context_max": "128K", '
        '"released": "2025-04-28", '
        '"notes": "key insight about this model"}}'
        "]}} "
        "Replace placeholders with real current data. Use null for unknown benchmarks. Return ONLY the JSON."
    ),
}

# ─── Enrichment Prompts (deeper analysis + links for modal) ──────────────────

ENRICHMENT_PROMPTS = {
    "models_deep": (
        "You are an AI researcher. Today is {date}. "
        "GOAL: Find the best local models and tools for 24/7 autonomous coding agents on 48-128GB unified memory or 24-32GB VRAM. "
        "Primary task: CODING. Need: 30B-70B inference + 7B-14B fine-tuning. "
        "Do deep web searches for each item below. Return ONLY JSON with detailed analysis and source links. "
        '{{"best_local_coding_models": {{"analysis": "2-3 paragraphs on the best local models for coding right now — any architecture (dense, MoE, hybrid). '
        "Include benchmark scores (HumanEval, SWE-bench, LiveCodeBench), tok/s on 128GB unified memory and 32GB VRAM, quantization options. "
        'Current baseline: Qwen3-30B-A3B ~160 tok/s on M4 Max. What beats this for coding?", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"fine_tuning_models": {{"analysis": "2-3 paragraphs on best 7B-14B models for coding fine-tuning. LoRA/QLoRA feasibility on 48-128GB RAM, training frameworks, datasets. Any breakthroughs?", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"inference_runtimes": {{"analysis": "Latest local inference runtime updates — ANY engine. Performance improvements, new model support, Apple Silicon + AMD ROCm/Vulkan optimizations, speculative decoding", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"coding_agent_frameworks": {{"analysis": "Latest autonomous coding frameworks. Which support local 30B-70B models best? New YOLO/unattended capabilities? Comparison of local-model performance across frameworks.", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}}} '
        "1-3 REAL URLs per item. Replace all placeholders with real current data. ONLY JSON."
    ),
    "hardware_deals_links": (
        "You are a hardware researcher. Today is {date}. "
        "GOAL: Best machine for local LLM inference (30B-70B) + fine-tuning (7B-14B). Budget INR 1.5-5L / USD 1500-5000. "
        "Search the web for each item. Return ONLY JSON with brief analysis and real source links: "
        '{{"mac_studio_m5": {{"analysis": "Latest M5 Mac Studio news, expected specs/price, release timeline. Better than M4 Max 128GB for LLM inference?", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}, '
        '"mac_studio_128gb_india": {{"analysis": "128GB Mac Studio availability in India. Alternative purchase: Amazon, Flipkart, refurbished, education pricing", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}, '
        '"strix_halo_options": {{"analysis": "Best Strix Halo 128GB mini PC for LLM inference. Framework vs Bosgame vs Beelink vs others. Price/performance, availability, LLM benchmarks", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}, '
        '"gpu_options": {{"analysis": "RTX 5090 32GB for LLM inference vs unified memory. Price in India, availability, tok/s for 30B-70B models. Worth it for fine-tuning?", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}, '
        '"apple_india_deals": {{"analysis": "Current Apple India deals, education offers, card cashback. Any way to get Mac Studio 128GB under INR 3.5L?", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}, '
        '"latest_local_llm_news": {{"analysis": "Top local LLM hardware news from Reddit and HN relevant to 30B-70B inference on unified memory or discrete GPU", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}}} '
        "1-3 REAL URLs per item. ONLY JSON."
    ),
    "efficiency_deep": (
        "You are an LLM efficiency researcher. Today is {date}. "
        "GOAL: Find breakthroughs in running large language models on budget/lower-end hardware. "
        "Do deep web searches for each item below. Return ONLY JSON with detailed analysis and source links: "
        '{{"quantization_breakthroughs": {{"analysis": "2-3 paragraphs on latest quantization methods reducing VRAM requirements. '
        "GPTQ, AWQ, GGUF improvements, new formats like HQQ, AQLM, QuIP#. Benchmark numbers: perplexity vs VRAM savings. "
        'What enables 30B+ models on 8-16GB VRAM?", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"inference_engine_updates": {{"analysis": "2-3 paragraphs on latest inference engine updates — llama.cpp, vLLM, KTransformers, PowerInfer, SGLang, exllamav2, TensorRT-LLM. '
        'Speed gains, new hardware support, memory optimizations. Include tok/s benchmarks where available.", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"moe_offloading": {{"analysis": "2-3 paragraphs on MoE expert offloading improvements. --n-cpu-moe in llama.cpp, heterogeneous CPU/GPU placement, '
        'partial offloading strategies. What MoE models can run on budget hardware now?", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"budget_gpu_benchmarks": {{"analysis": "2-3 paragraphs on budget GPU LLM benchmarks. RTX 4060 Ti 16GB, RTX 3060 12GB, older GPUs running 30B+ models. '
        'Practical configs, quantization settings, tok/s numbers.", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"efficient_model_architectures": {{"analysis": "2-3 paragraphs on efficient model architectures — MoE with few active params (Qwen3-30B-A3B), sparse models, '
        'distilled models. Which architectures give best quality per VRAM GB?", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"memory_optimization": {{"analysis": "2-3 paragraphs on memory optimization tricks — KV cache compression, speculative decoding, flash attention improvements, '
        'paged attention. What reduces memory footprint most for long-context inference?", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"community_discoveries": {{"analysis": "2-3 paragraphs on community discoveries from r/LocalLLaMA, YouTube demos, GitHub PRs. '
        'Real-world configs people use to run big models on small GPUs. Most upvoted/discussed findings.", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}}} '
        "1-3 REAL URLs per item. Replace all placeholders with real current data. ONLY JSON."
    ),
    "learning_deep": (
        "You are a local LLM learning curator. Today is {date}. "
        "GOAL: Provide deeper analysis of the top 3 most valuable learning resources about local LLMs from the past 7 days. "
        "Search Reddit r/LocalLLaMA, YouTube, Hacker News, tech blogs, and GitHub for the best educational content. "
        "Do deep web searches for each item. Return ONLY JSON with detailed analysis and source links: "
        '{{"top_learning_item_1": {{"analysis": "2-3 paragraphs with full summary of the content, key takeaways, and why it matters for someone running LLMs locally. '
        'Cover technical details, practical implications, and how to apply the knowledge.", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"top_learning_item_2": {{"analysis": "2-3 paragraphs with full summary — different topic from item 1. '
        'Focus on actionable insights for local LLM users: fine-tuning, inference optimization, agent frameworks, or hardware tips.", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"top_learning_item_3": {{"analysis": "2-3 paragraphs with full summary — different topic from items 1-2. '
        'Prioritize content with genuine educational depth over surface-level news.", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}}} '
        "1-3 REAL URLs per item. Replace all placeholders with real current data. ONLY JSON."
    ),
}

# ─── Helper Functions ────────────────────────────────────────────────────────

def load_state() -> dict:
    """Load previous monitoring state."""
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not load state file, starting fresh")
    return {"last_run": None, "checks": {}, "history": []}


def save_state(state: dict):
    """Save current monitoring state."""
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


def run_copilot(prompt: str, timeout: int = 180, category: str = "general") -> str:
    """Run a Copilot CLI prompt and return the raw response text.
    
    Calls node directly with the npm-loader.js script, bypassing the .cmd
    wrapper to avoid cmd.exe metacharacter interpretation issues on Windows.
    Creates a named session per category so it can be resumed later via
    'copilot --resume="llm-monitor-{category}-{date}-{hex}"'.
    """
    session_name = _session_name_for(category)
    cmd = [COPILOT_CMD, COPILOT_SCRIPT, "-p", prompt] + COPILOT_FLAGS
    cmd += ["--name", session_name]
    logger.info(f"Running Copilot prompt ({len(prompt)} chars) [session={session_name}]...")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            stderr = result.stderr.lower() if result.stderr else ""
            if "auth" in stderr or "login" in stderr:
                logger.error("Copilot auth expired! Run 'copilot login' to re-authenticate.")
            else:
                logger.error(f"Copilot exited with code {result.returncode}")
                logger.error(f"stderr: {result.stderr[:500]}")
            return ""

        output = result.stdout.strip()
        if not output:
            logger.warning("Empty stdout from Copilot")
        else:
            logger.info(f"Got {len(output)} chars of output")
        return output

    except subprocess.TimeoutExpired:
        logger.error(f"Copilot timed out after {timeout}s")
        return ""
    except FileNotFoundError:
        logger.error("Copilot CLI not found! Is node installed and in PATH?")
        return ""
    except Exception as e:
        logger.error(f"Copilot invocation failed: {e}")
        return ""


def parse_json_response(text: str) -> dict | None:
    """Extract JSON from Copilot response, handling tool progress indicators.
    
    The response text typically looks like:
    ● Web Search... ● Web Search... {"key": "value", ...}
    
    Strategy: find the last JSON object in the text (the actual response),
    ignoring intermediate JSON fragments from tool outputs.
    """
    if not text:
        return None

    # Strip markdown code fences
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = cleaned.strip()

    # Strategy 1: Try to find JSON from the last occurrence of a top-level opening brace
    # by scanning backwards for valid JSON
    best_result = None
    search_start = 0
    
    # Find all positions where a { starts a potential JSON object
    # We look for patterns like {"key" which indicate a JSON object start
    for match in re.finditer(r'\{"[a-zA-Z_]', cleaned):
        pos = match.start()
        # Try to parse from this position
        candidate = cleaned[pos:]
        # Find the matching closing brace by trying progressively longer substrings
        depth = 0
        in_string = False
        escape_next = False
        end_pos = None
        for i, ch in enumerate(candidate):
            if escape_next:
                escape_next = False
                continue
            if ch == '\\' and in_string:
                escape_next = True
                continue
            if ch == '"' and not escape_next:
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    end_pos = i
                    break
        
        if end_pos is not None:
            json_str = candidate[:end_pos + 1]
            try:
                parsed = json.loads(json_str)
                if isinstance(parsed, dict) and len(parsed) >= 2:
                    # Prefer JSON objects with more keys (the full response)
                    if best_result is None or len(parsed) > len(best_result):
                        best_result = parsed
            except json.JSONDecodeError:
                continue

    if best_result:
        return best_result

    # Strategy 2: Try direct parse of the whole text (unlikely but cheap)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    logger.warning(f"Could not parse JSON from response ({len(text)} chars)")
    return None


# Expected keys per category — used for schema validation on parse
EXPECTED_KEYS = {
    "hardware": {"mac_studio_m5", "mac_studio_128gb_india", "mac_studio_128gb_us"},
    "models_and_agents": {"best_local_coding_models", "inference_runtimes"},
    "deals_and_blogs": {"apple_india_deals", "latest_local_llm_news"},
    "efficiency_research": {"quantization_breakthroughs", "inference_engine_updates", "moe_offloading"},
    "learning_feed": {"articles", "title"},
    "model_benchmarks": {"top_coding_models", "name"},
}


def _validate_response_schema(parsed: dict, category: str) -> bool:
    """Check if parsed JSON has minimum expected keys for the category."""
    expected = EXPECTED_KEYS.get(category)
    if not expected:
        return True  # no schema defined, accept anything
    present = set(parsed.keys())
    overlap = present & expected
    # Require at least half the expected keys
    if len(overlap) >= len(expected) / 2:
        return True
    # Fallback: for learning_feed, accept flat dict of articles (each has title+url)
    if category == "learning_feed" and len(parsed) >= 3:
        first_val = next(iter(parsed.values()))
        if isinstance(first_val, dict) and "title" in first_val:
            return True
    return False


def run_copilot_with_retry(prompt: str, category: str = None,
                           timeout: int = 180, max_retries: int = 2) -> tuple[str, dict | None]:
    """Run Copilot CLI and parse JSON, retrying on parse failure or schema mismatch.

    Returns (raw_response, parsed_dict_or_None).
    """
    response = ""
    for attempt in range(max_retries):
        if attempt > 0:
            time.sleep(3)  # brief pause before retry to avoid race conditions
        response = run_copilot(prompt, timeout=timeout, category=category or "general")
        if not response:
            logger.warning(f"Attempt {attempt + 1}/{max_retries}: empty response")
            continue

        parsed = parse_json_response(response)
        if parsed:
            if category and not _validate_response_schema(parsed, category):
                logger.warning(
                    f"Attempt {attempt + 1}/{max_retries}: schema mismatch for {category} "
                    f"(got keys: {list(parsed.keys())[:5]})"
                )
                continue
            return response, parsed

        logger.warning(f"Attempt {attempt + 1}/{max_retries}: JSON parse failed")

    return response if response else "", None


# ─── Store Availability Checker (Playwright) ─────────────────────────────────

STORE_CHECK_CONFIGS = [
    # ── Apple Mac Studio (HIGHEST PRIORITY) ──
    {
        "key": "mac_studio_128gb_india",
        "label": "Mac Studio M4 Max 128GB/512GB (Apple India)",
        "url": "https://www.apple.com/in/shop/buy-mac/mac-studio/m4-max-chip-16-core-cpu-40-core-gpu-128gb-memory-512gb-storage",
        "store": "apple",
        "currency": "INR",
    },
    {
        "key": "mac_studio_128gb_india_1tb",
        "label": "Mac Studio M4 Max 128GB/1TB (Apple India)",
        "url": "https://www.apple.com/in/shop/buy-mac/mac-studio/m4-max-chip-16-core-cpu-40-core-gpu-128gb-memory-1tb-storage",
        "store": "apple",
        "currency": "INR",
    },
    {
        "key": "mac_studio_128gb_us",
        "label": "Mac Studio M4 Max 128GB/512GB (Apple US)",
        "url": "https://www.apple.com/shop/buy-mac/mac-studio/m4-max-chip-16-core-cpu-40-core-gpu-128gb-memory-512gb-storage",
        "store": "apple",
        "currency": "USD",
    },
    # ── Apple Mac Mini (budget option) ──
    {
        "key": "mac_mini_48gb_india",
        "label": "Mac Mini M4 Pro 48GB/512GB (Apple India)",
        "url": "https://www.apple.com/in/shop/buy-mac/mac-mini/m4-pro-chip-12-core-cpu-16-core-gpu-48gb-memory-512gb-storage",
        "store": "apple",
        "currency": "INR",
    },
    {
        "key": "mac_mini_48gb_us",
        "label": "Mac Mini M4 Pro 48GB/512GB (Apple US)",
        "url": "https://www.apple.com/shop/buy-mac/mac-mini/m4-pro-chip-12-core-cpu-16-core-gpu-48gb-memory-512gb-storage",
        "store": "apple",
        "currency": "USD",
    },
    # ── Apple Refurbished ──
    {
        "key": "apple_refurbished_india",
        "label": "Mac Studio Refurbished (Apple India)",
        "url": "https://www.apple.com/in/shop/refurbished/mac/mac-studio",
        "store": "generic",
        "currency": "INR",
        "search_terms": ["Mac Studio", "128GB"],
        "out_of_stock_phrases": ["no products", "0 results", "no items"],
    },
    {
        "key": "apple_refurbished_us",
        "label": "Mac Studio Refurbished (Apple US)",
        "url": "https://www.apple.com/shop/refurbished/mac/mac-studio",
        "store": "generic",
        "currency": "USD",
        "search_terms": ["Mac Studio", "128GB"],
        "out_of_stock_phrases": ["no products", "0 results", "no items"],
    },
    # ── AMD Strix Halo: Framework Desktop (best value, modular) ──
    {
        "key": "framework_desktop_128gb",
        "label": "Framework Desktop Strix Halo 128GB (Framework US)",
        "url": "https://frame.work/desktop",
        "store": "generic",
        "currency": "USD",
        "search_terms": ["128GB", "Max+ 395", "Desktop"],
    },
    # ── AMD Strix Halo: Bosgame M5 (cheapest Strix Halo) ──
    {
        "key": "bosgame_m5_128gb",
        "label": "Bosgame M5 Strix Halo 128GB (Bosgame Official)",
        "url": "https://www.bosgamepc.com/products/bosgame-m5-ai-mini-desktop-ryzen-ai-max-395",
        "store": "generic",
        "currency": "USD",
        "search_terms": ["128GB", "Max+ 395", "M5"],
    },
    # ── AMD Strix Halo: Beelink GTR9 Pro ──
    {
        "key": "beelink_gtr9_pro_128gb",
        "label": "Beelink GTR9 Pro Strix Halo 128GB (Beelink Official)",
        "url": "https://www.bee-link.com/products/beelink-gtr9-pro-amd-ryzen-ai-max-395-processor-openclaw",
        "store": "generic",
        "currency": "USD",
        "search_terms": ["128GB", "GTR9", "Max+ 395"],
    },
    # ── AMD Strix Halo: Minisforum MS-S1 Max ──
    {
        "key": "minisforum_ms_s1_max",
        "label": "Minisforum MS-S1 Max Strix Halo 128GB (Minisforum Store)",
        "url": "https://store.minisforum.com/products/minisforum-ms-s1-max-mini-pc",
        "store": "generic",
        "currency": "USD",
        "search_terms": ["128GB", "MS-S1", "Max+ 395"],
    },
    # ── AMD Strix Halo: Corsair AI Workstation 300 ──
    {
        "key": "corsair_ws300",
        "label": "Corsair AI WS 300 Strix Halo 128GB (Corsair US)",
        "url": "https://www.corsair.com/us/en/p/gaming-computers/cs-9080003-na/corsair-ai-workstation-300-amd-ryzen-ai-max-395-processor-amd-radeon-8060s-igpu-up-to-96gb-vram-128gb-lpddr5x-memory-4tb-2tb-2tb-m2-ssd-win11-home-cs-9080003-na",
        "store": "generic",
        "currency": "USD",
        "search_terms": ["AI Workstation", "128GB", "Max+ 395"],
    },
    # ── AMD Strix Halo: ASUS ProArt PX13 (Amazon India) ──
    {
        "key": "amd_strix_halo_128gb_india",
        "label": "ASUS ProArt PX13 Strix Halo 128GB (Amazon India)",
        "url": "https://www.amazon.in/ASUS-ProArt-Radeon-HN7306EAC-LX052WS-Creator/dp/B0GLFHMJXL",
        "store": "amazon",
        "currency": "INR",
        "search_terms": ["128 GB", "Strix", "ProArt"],
    },
    # ── GPU: RTX 5090 (Amazon India — Founders Edition) ──
    {
        "key": "rtx_5090_india",
        "label": "NVIDIA RTX 5090 Founders Edition (Amazon India)",
        "url": "https://www.amazon.in/Nvidia-GeForce-RTX-5090-Founders/dp/B0DYDY8KSC",
        "store": "amazon",
        "currency": "INR",
        "search_terms": ["RTX 5090", "GeForce", "32 GB"],
    },
    # ── GPU: RTX 5090 (Flipkart India — MSI Vanguard) ──
    {
        "key": "rtx_5090_flipkart",
        "label": "RTX 5090 MSI Vanguard 32GB (Flipkart India)",
        "url": "https://www.flipkart.com/msi-rtx-5090-32g-vanguard-soc-gddr6x-32-gb-nvidia-chipset-256-bit-2730-mhz-graphics-card/p/itmd6561ebac286a",
        "store": "generic",
        "currency": "INR",
        "search_terms": ["RTX 5090", "MSI", "32 GB"],
    },
]


# ─── Dynamic Store Discovery & Evolution ─────────────────────────────────────

# === USER GOAL (anchors all discovery and monitoring) ===
# Machine for 24/7 local LLM coding agents with good reasoning.
#
# RESEARCH HISTORY (what we already investigated and concluded):
# - RTX 4090: DISCONTINUED Oct 2024, not available new in India
# - RTX 4070 Ti Super: 16GB VRAM, caps at 14B models — insufficient for 30B+
# - RTX 5080: 16GB VRAM, same 14B cap — insufficient
# - RTX 5090: 32GB VRAM, can do 32B+128k context, but ₹2.83-5.5L in India
# - Dual GPU: unreliable, Ollama no support, llama.cpp tensor parallelism 1.5x only
# - Mac Mini M4 Pro 48GB: ₹1.86-2.04L India, runs 32B+128k — solid budget option
# - Mac Studio M4 Max 128GB: ₹3.45-3.65L India, runs 70B — best overall but was OOS
# - Mac Studio M5: expected WWDC June 2026, ship ~Oct 2026
# - Bosgame M5 96GB Strix Halo: $1,589-2,599, 66-72 tok/s MoE, cheapest 32B+128k
# - Minisforum MS-S1 Max 128GB: $2,299, ships ~Oct 2025
# - Corsair AI WS 300: $2,699 — same price as Mac Studio but 2.3x slower
# - MoE game-changer: Qwen3-30B-A3B (3B active) = 66-72 tok/s on Strix Halo, ~160 on M4 Max
# - Cloud cost: Sonnet 4.6 24/7 = ₹1.32 Cr/year vs local ₹7,200/year
# - Quality gap: Sonnet 4.6 ~80% SWE-bench vs Qwen3-30B MoE ~45% hard tasks
# - Hybrid approach: local for 70% routine work, cloud for 30% hard tasks
# - Apple warranty: worldwide, works in India for US purchases
# - Exchange rate: 1 USD = ₹94.13 (NOT ₹85)
#
# DECISION: Wait for Mac Studio M5 Max 128GB (expected Oct 2026).
# Meanwhile monitor: availability changes, price drops, new Strix Halo options,
# better models, and any game-changing developments.
#
USER_CONSTRAINTS = {
    "min_memory_gb": 48,         # minimum useful memory for 30B+ models
    "ideal_memory_gb": 128,      # ideal for 70B models + fine-tuning
    "min_vram_gb": 24,           # minimum discrete GPU VRAM (only RTX 5090 qualifies)
    "budget_inr_min": 150_000,
    "budget_inr_max": 500_000,   # stretch budget for exceptional value
    "budget_usd_min": 1_500,
    "budget_usd_max": 5_000,
    "use_cases": [
        "inference_30b_70b",       # primary: run 30B-70B models for coding agents
        "fine_tuning_7b_14b",      # secondary: fine-tune 7B-14B coding models
        "coding_agents_24x7",      # must run 24/7 unattended
        "moe_models",              # MoE models (Qwen3-30B-A3B) are game-changers on unified memory
    ],
    "buy_regions": ["India", "USA", "Canada"],
    "current_plan": "wait_for_mac_studio_m5",
    "fallback_options": [
        "mac_studio_m4_max_128gb_if_restocked",
        "bosgame_m5_128gb_if_price_drops",
        "framework_desktop_128gb",
    ],
    # Items we've already ruled out (don't re-discover these)
    "ruled_out": [
        "rtx_4090",          # discontinued
        "rtx_4070_ti_super", # 16GB too small for 30B+
        "rtx_5080",          # 16GB too small for 30B+
        "dual_gpu",          # unreliable, poor software support
        "olx",               # user explicitly said no OLX
    ],
}

# Relevance keywords — product must match at least one to be discovered
RELEVANCE_KEYWORDS = [
    # Memory indicators (unified/shared ≥48GB)
    "128gb", "96gb", "64gb", "48gb", "unified memory", "shared memory",
    # GPU VRAM indicators (≥24GB)
    "24gb vram", "32gb vram", "48gb vram", "rtx 5090", "rtx 4090", "rtx 5080",
    # Chip families relevant to local LLM
    "strix halo", "ryzen ai max", "apple silicon", "m4 max", "m4 ultra",
    "m5 max", "m5 ultra", "m4 pro",
    # Product types
    "mac studio", "mac mini", "mini pc", "ai workstation", "ai desktop",
    # Use case signals
    "local llm", "llm inference", "fine-tuning", "fine tuning",
    "coding agent", "ai pc",
]

# Trusted domains for auto-discovered store URLs
TRUSTED_STORE_DOMAINS = {
    "apple.com", "amazon.in", "amazon.com", "flipkart.com",
    "frame.work", "bosgamepc.com", "bee-link.com", "beelink.com",
    "minisforum.com", "store.minisforum.com", "corsair.com",
    "in.store.asus.com", "asus.com", "newegg.com", "bestbuy.com",
    "bhphotovideo.com", "nvidia.com", "mdcomputers.in", "primeabgb.com",
    "elitehubs.com", "vedantcomputers.com", "theitdepot.com",
    "croma.com", "reliance.digital", "pcstudio.in",
}


# ─── Capability Sheet (structured workload-fit data per candidate) ───────────

# Seed data from our prior research — updated by enrichment runs
CAPABILITY_SHEET_SEED = {
    "mac_studio_m4_max_128gb": {
        "name": "Mac Studio M4 Max 128GB",
        "memory_gb": 128, "memory_type": "unified", "bandwidth_gbs": 546,
        "chip": "M4 Max", "gpu_cores": 40,
        "tok_s_30b_q4": 160, "tok_s_70b_q4": 28, "model_used": "Qwen3-30B-A3B / Llama-70B",
        "max_context_30b": 128000, "max_context_70b": 32000,
        "fine_tune_7b": "QLoRA feasible", "fine_tune_14b": "QLoRA tight, ~2 tok/s training",
        "power_idle_w": 20, "power_load_w": 185, "noise": "fan audible under sustained load",
        "always_on_suitability": "excellent", "os": "macOS",
        "price_inr": 364900, "price_usd": 3499,
        "buy_regions": ["India", "USA", "Canada"], "warranty": "global Apple warranty",
    },
    "mac_mini_m4_pro_48gb": {
        "name": "Mac Mini M4 Pro 48GB",
        "memory_gb": 48, "memory_type": "unified", "bandwidth_gbs": 273,
        "chip": "M4 Pro", "gpu_cores": 20,
        "tok_s_30b_q4": 80, "tok_s_70b_q4": None, "model_used": "Qwen3-30B-A3B",
        "max_context_30b": 64000, "max_context_70b": None,
        "fine_tune_7b": "QLoRA feasible", "fine_tune_14b": "not feasible (memory)",
        "power_idle_w": 7, "power_load_w": 65, "noise": "very quiet",
        "always_on_suitability": "excellent", "os": "macOS",
        "price_inr": 189900, "price_usd": 1799,
        "buy_regions": ["India", "USA", "Canada"], "warranty": "global Apple warranty",
    },
    "bosgame_m5_128gb": {
        "name": "Bosgame M5 Strix Halo 128GB",
        "memory_gb": 128, "memory_type": "unified", "bandwidth_gbs": 256,
        "chip": "Ryzen AI Max+ 395", "gpu_cores": 40,
        "tok_s_30b_q4": 70, "tok_s_70b_q4": 15, "model_used": "Qwen3-30B-A3B / Llama-70B",
        "max_context_30b": 128000, "max_context_70b": 16000,
        "fine_tune_7b": "QLoRA feasible (ROCm)", "fine_tune_14b": "QLoRA possible but slow",
        "power_idle_w": 25, "power_load_w": 120, "noise": "moderate fan noise",
        "always_on_suitability": "good", "os": "Windows/Linux",
        "price_inr": None, "price_usd": 2599,
        "buy_regions": ["USA"], "warranty": "1yr manufacturer (no India service)",
    },
    "framework_desktop_128gb": {
        "name": "Framework Desktop Strix Halo 128GB",
        "memory_gb": 128, "memory_type": "unified", "bandwidth_gbs": 256,
        "chip": "Ryzen AI Max+ 395", "gpu_cores": 40,
        "tok_s_30b_q4": 70, "tok_s_70b_q4": 15, "model_used": "Qwen3-30B-A3B / Llama-70B",
        "max_context_30b": 128000, "max_context_70b": 16000,
        "fine_tune_7b": "QLoRA feasible (ROCm)", "fine_tune_14b": "QLoRA possible but slow",
        "power_idle_w": None, "power_load_w": None, "noise": "unknown",
        "always_on_suitability": "likely good", "os": "Windows/Linux",
        "price_inr": None, "price_usd": 1999,
        "buy_regions": ["USA"], "warranty": "Framework warranty (no India service)",
    },
    "mac_studio_m5_max_128gb": {
        "name": "Mac Studio M5 Max 128GB (expected)",
        "memory_gb": 128, "memory_type": "unified", "bandwidth_gbs": 614,
        "chip": "M5 Max (expected)", "gpu_cores": 40,
        "tok_s_30b_q4": None, "tok_s_70b_q4": None, "model_used": "estimated ~12% faster than M4 Max",
        "max_context_30b": None, "max_context_70b": None,
        "fine_tune_7b": "QLoRA feasible (expected)", "fine_tune_14b": "likely feasible",
        "power_idle_w": None, "power_load_w": None, "noise": "expected similar to M4",
        "always_on_suitability": "expected excellent", "os": "macOS",
        "price_inr": None, "price_usd": None,
        "buy_regions": ["India", "USA", "Canada"], "warranty": "global Apple warranty",
    },
}


def compute_readiness_score(state: dict, checks: dict) -> dict:
    """Compute a 0-100 readiness score for running 24/7 local coding agents.

    Evaluates hardware, models, tools, and cost dimensions and returns
    a structured dict with per-dimension scores, statuses, summaries,
    blockers, and an overall weighted score with trend detection.
    """
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    # ── Hardware (weight 40%) ────────────────────────────────────────────
    hw_score = 0
    hw_status = "none"
    hw_summary = "No viable hardware options identified"
    hw_blockers: list[str] = []

    current_plan = USER_CONSTRAINTS.get("current_plan", "")
    rec = state.get("recommendation", {})
    sheet = state.get("capability_sheet", {})
    store_check = state.get("store_check", {})
    store_results = store_check.get("results", [])

    # Count viable options from capability sheet
    viable_options = [
        k for k, v in sheet.items()
        if isinstance(v, dict) and (v.get("memory_gb") or 0) >= 48
    ]

    # Check if any tracked store item is in stock / orderable
    any_in_stock = any(
        r.get("in_stock") or r.get("orderable")
        for r in store_results if isinstance(r, dict)
    )

    # Determine best option label and price from recommendation or sheet
    best_label = rec.get("best_option", "")
    best_price = ""
    if not best_label and viable_options:
        first = sheet.get(viable_options[0], {})
        best_label = first.get("name", viable_options[0])
    for v in sheet.values():
        if isinstance(v, dict) and v.get("name") == best_label:
            usd = v.get("price_usd")
            inr = v.get("price_inr")
            if usd:
                best_price = f"${usd:,}"
            elif inr:
                best_price = f"₹{inr:,}"
            break

    plan_lower = current_plan.lower().replace(" ", "_") if current_plan else ""

    if "running" in plan_lower or "serving" in plan_lower:
        hw_score = 100
        hw_status = "ready"
        hw_summary = f"Hardware running and serving models"
    elif "setup" in plan_lower or "received" in plan_lower:
        hw_score = 80
        hw_status = "ready"
        hw_summary = f"Hardware received, setting up"
    elif "ordered" in plan_lower or "shipping" in plan_lower:
        hw_score = 60
        hw_status = "ordered"
        hw_summary = f"Hardware ordered — {best_label}"
        hw_blockers.append("Waiting for delivery")
    elif "selected" in plan_lower or "buy_now" in plan_lower:
        hw_score = 40
        hw_status = "shopping"
        hw_summary = f"Selected: {best_label}" + (f" at {best_price}" if best_price else "")
        hw_blockers.append("Purchase not yet made")
    elif viable_options:
        hw_score = 20
        hw_status = "shopping"
        count = len(viable_options)
        price_bit = f". Best: {best_label}" + (f" at {best_price}" if best_price else "")
        hw_summary = f"Monitoring {count} option{'s' if count != 1 else ''}{price_bit}"
        hw_blockers.append("No hardware purchased yet")
    else:
        hw_blockers.append("No viable options identified")

    if any_in_stock and hw_score < 40:
        hw_score = min(hw_score + 10, 40)

    # ── Models (weight 25%) ──────────────────────────────────────────────
    model_score = 25  # baseline — some local models exist
    model_status = "weak"
    model_summary = "No coding model data available"
    model_blockers: list[str] = []

    best_swe = 0.0
    best_model_name = ""
    best_tok_s = 0
    fits_48gb = False

    # Pull from model_benchmarks top_coding_models list
    benchmarks = checks.get("model_benchmarks", {})
    top_models = benchmarks.get("top_coding_models", [])
    if isinstance(top_models, list):
        for m in top_models:
            if not isinstance(m, dict):
                continue
            swe = m.get("swe_bench_verified") or m.get("swe_bench") or 0
            try:
                swe = float(swe)
            except (ValueError, TypeError):
                swe = 0
            if swe > best_swe:
                best_swe = swe
                best_model_name = m.get("name", "unknown")
                # Check tok/s on target hardware
                for tok_key in ("tok_s_128gb_unified", "tok_s_48gb_unified", "tok_s_rtx5090"):
                    val = m.get(tok_key)
                    if val:
                        try:
                            best_tok_s = max(best_tok_s, int(val))
                        except (ValueError, TypeError):
                            pass
                # Check memory fit
                vram_q4 = str(m.get("vram_q4", "")) .lower().replace("gb", "").strip()
                try:
                    if float(vram_q4) <= 48:
                        fits_48gb = True
                except (ValueError, TypeError):
                    pass

    # Also check models_and_agents for info strings with benchmark hints
    ma = checks.get("models_and_agents", {})
    if isinstance(ma, dict):
        coding_info = ""
        cm = ma.get("best_local_coding_models", {})
        if isinstance(cm, dict):
            coding_info = cm.get("info", "")
        # Try to extract SWE-bench percentages from info text
        import re as _re
        swe_matches = _re.findall(r'(\d+(?:\.\d+)?)\s*%?\s*(?:on\s+)?swe[_-]?bench', coding_info, _re.IGNORECASE)
        for s in swe_matches:
            try:
                val = float(s)
                if val > best_swe:
                    best_swe = val
            except (ValueError, TypeError):
                pass

    # Score based on best SWE-bench
    if best_swe >= 60:
        model_score = 100
        model_status = "excellent"
    elif best_swe >= 45:
        model_score = 70
        model_status = "good_enough"
    elif best_swe >= 30:
        model_score = 50
        model_status = "good_enough"
    elif best_swe > 0:
        model_score = 25
        model_status = "weak"

    # Bonuses
    if best_tok_s >= 50:
        model_score = min(model_score + 10, 100)
    if fits_48gb:
        model_score = min(model_score + 10, 100)

    if best_model_name:
        swe_str = f"{best_swe:.0f}%" if best_swe else "N/A"
        tok_str = f", {best_tok_s} tok/s" if best_tok_s else ""
        model_summary = f"{best_model_name} achieves ~{swe_str} SWE-bench{tok_str}"
    if best_swe < 60:
        model_blockers.append("No model above 60% SWE-bench runs locally yet")

    # ── Tools (weight 15%) ───────────────────────────────────────────────
    tools_score = 30  # baseline — basic frameworks exist
    tools_status = "basic"
    tools_summary = "Basic coding agent frameworks available"
    tools_blockers: list[str] = []

    frameworks = {}
    if isinstance(ma, dict):
        frameworks = ma.get("coding_agent_frameworks", {})
    if not isinstance(frameworks, dict):
        frameworks = {}

    fw_info = (frameworks.get("info", "") or "").lower()

    # Look for signals of maturity in the framework info
    production_signals = ["battle-tested", "production", "reliable 24/7", "stable yolo"]
    yolo_signals = ["yolo", "unattended", "autonomous", "auto-accept", "headless"]
    local_signals = ["local model", "local llm", "ollama", "llama.cpp", "30b", "70b"]

    has_yolo = any(s in fw_info for s in yolo_signals)
    has_local = any(s in fw_info for s in local_signals)
    has_production = any(s in fw_info for s in production_signals)

    if has_production and has_yolo and has_local:
        tools_score = 100
        tools_status = "production"
        tools_summary = "Production-ready YOLO frameworks with local model support"
    elif has_yolo and has_local:
        tools_score = 80
        tools_status = "maturing"
        tools_summary = "YOLO mode available with local 30B+ model support"
    elif has_yolo or has_local:
        tools_score = 55
        tools_status = "maturing"
        tools_summary = "Frameworks support local models in autonomous mode"
        tools_blockers.append("Most frameworks optimized for cloud APIs, not local")
    else:
        tools_blockers.append("Limited autonomous/YOLO support for local models")

    # Also check enrichment for deeper analysis
    enrichment_fw = state.get("enrichment", {}).get("coding_agent_frameworks", {})
    if isinstance(enrichment_fw, dict):
        analysis = (enrichment_fw.get("analysis", "") or "").lower()
        if any(s in analysis for s in yolo_signals) and tools_score < 55:
            tools_score = 55
            tools_status = "maturing"

    # ── Cost (weight 20%) ────────────────────────────────────────────────
    cost_score = 0
    cost_status = "over_budget"
    cost_summary = "No viable option found"
    cost_blockers: list[str] = []

    budget_usd_max = USER_CONSTRAINTS.get("budget_usd_max", 5000)
    budget_inr_max = USER_CONSTRAINTS.get("budget_inr_max", 500_000)
    budget_inr_min = USER_CONSTRAINTS.get("budget_inr_min", 150_000)
    budget_usd_min = USER_CONSTRAINTS.get("budget_usd_min", 1500)

    # Find cheapest viable option
    best_usd = None
    best_inr = None
    best_cost_label = ""
    for k, v in sheet.items():
        if not isinstance(v, dict) or (v.get("memory_gb") or 0) < 48:
            continue
        try:
            usd = float(v["price_usd"]) if v.get("price_usd") else None
        except (ValueError, TypeError):
            usd = None
        try:
            inr = float(v["price_inr"]) if v.get("price_inr") else None
        except (ValueError, TypeError):
            inr = None
        if usd and (best_usd is None or usd < best_usd):
            best_usd = usd
            best_cost_label = v.get("name", k)
        if inr and (best_inr is None or inr < best_inr):
            best_inr = inr
            if not best_usd or (usd and usd >= best_usd):
                best_cost_label = v.get("name", k)

    # Score against budget thresholds
    under_budget = False
    if best_usd is not None:
        if best_usd <= budget_usd_min:
            cost_score = 100
            cost_status = "great_value"
        elif best_usd <= 3000:
            cost_score = 75
            cost_status = "within_budget"
        elif best_usd <= budget_usd_max:
            cost_score = 50
            cost_status = "within_budget"
        else:
            cost_score = 25
            cost_status = "over_budget"
            cost_blockers.append(f"Best USD option ${best_usd:,} exceeds ${budget_usd_max:,}")
        under_budget = best_usd <= budget_usd_max

    if best_inr is not None:
        if best_inr <= budget_inr_min:
            inr_score = 100
        elif best_inr <= 300_000:
            inr_score = 75
        elif best_inr <= budget_inr_max:
            inr_score = 50
        else:
            inr_score = 25
        if inr_score > cost_score:
            cost_score = inr_score
            cost_status = "great_value" if inr_score >= 100 else "within_budget" if inr_score >= 50 else "over_budget"
        under_budget = under_budget or (best_inr <= budget_inr_max)

    if best_cost_label:
        price_parts = []
        if best_inr:
            price_parts.append(f"₹{best_inr / 100_000:.2f}L")
        if best_usd:
            price_parts.append(f"${best_usd:,}")
        cost_summary = f"Best option: {best_cost_label} at {' / '.join(price_parts)}" if price_parts else f"Best option: {best_cost_label}"
    elif not under_budget:
        cost_blockers.append("No viable option within budget")

    # ── Overall (weighted average) ───────────────────────────────────────
    overall = int(
        hw_score * 0.40
        + model_score * 0.25
        + tools_score * 0.15
        + cost_score * 0.20
    )
    overall = max(0, min(100, overall))

    # ── Trend detection ──────────────────────────────────────────────────
    trend = "stable"
    history = state.get("readiness_history", [])
    if history:
        last_overall = history[-1].get("overall", overall)
        if overall > last_overall + 2:
            trend = "improving"
        elif overall < last_overall - 2:
            trend = "declining"

    return {
        "overall": overall,
        "hardware": {
            "score": hw_score,
            "status": hw_status,
            "summary": hw_summary,
            "blockers": hw_blockers,
        },
        "models": {
            "score": model_score,
            "status": model_status,
            "summary": model_summary,
            "blockers": model_blockers,
        },
        "tools": {
            "score": tools_score,
            "status": tools_status,
            "summary": tools_summary,
            "blockers": tools_blockers,
        },
        "cost": {
            "score": cost_score,
            "status": cost_status,
            "summary": cost_summary,
            "blockers": cost_blockers,
        },
        "timestamp": now_iso,
        "trend": trend,
    }


def update_capability_sheet(state: dict, store_results: list[dict] = None,
                            enrichment: dict = None) -> dict:
    """Update the persistent capability sheet from store results and enrichment.

    Merges new data into existing sheet, never overwriting with None.
    """
    sheet = state.get("capability_sheet", {})

    # Seed with defaults for any missing candidates
    for key, seed in CAPABILITY_SHEET_SEED.items():
        if key not in sheet:
            sheet[key] = dict(seed)

    # Update prices from store results
    if store_results:
        store_to_sheet = {
            "mac_studio_128gb_india": "mac_studio_m4_max_128gb",
            "mac_studio_128gb_us": "mac_studio_m4_max_128gb",
            "mac_mini_48gb_india": "mac_mini_m4_pro_48gb",
            "mac_mini_48gb_us": "mac_mini_m4_pro_48gb",
            "bosgame_m5_128gb": "bosgame_m5_128gb",
            "framework_desktop_128gb": "framework_desktop_128gb",
        }
        for result in store_results:
            sheet_key = store_to_sheet.get(result["key"])
            if not sheet_key or sheet_key not in sheet:
                continue
            if result.get("price"):
                price_field = "price_inr" if result["currency"] == "INR" else "price_usd"
                sheet[sheet_key][price_field] = result["price"]
            sig = result.get("availability_signal", "")
            if sig in ("config_validated", "add_to_cart", "in_stock_schema"):
                sheet[sheet_key]["currently_available"] = True
            elif sig == "out_of_stock":
                sheet[sheet_key]["currently_available"] = False

    # Update from enrichment analysis
    if enrichment:
        for key, enr_data in enrichment.items():
            if not isinstance(enr_data, dict):
                continue
            analysis = enr_data.get("analysis", "")
            if not analysis:
                continue
            for sheet_key in sheet:
                if sheet_key.replace("_", " ") in key.replace("_", " ") or key.replace("_", " ") in sheet_key.replace("_", " "):
                    sheet[sheet_key]["last_enrichment"] = analysis[:500]
                    sheet[sheet_key]["enrichment_date"] = datetime.now().strftime("%Y-%m-%d")
                    break

    state["capability_sheet"] = sheet
    return state


# ─── Model Benchmarks Processing ────────────────────────────────────────────

# VRAM budgets (GB) for each hardware config
_HARDWARE_VRAM = {
    "128gb_unified": 128,
    "48gb_unified": 48,
    "rtx5090_32gb": 32,
    "rtx4060ti_16gb_moe": 16,
}


def process_model_benchmarks(state: dict, checks: dict) -> dict:
    """Extract and track model benchmark data from check results."""
    bm_data = checks.get("model_benchmarks", {})
    top_models = bm_data.get("top_coding_models", [])

    models_list: list[dict] = []
    if isinstance(top_models, list):
        models_list = top_models
    elif isinstance(top_models, dict):
        # Fallback: dict keyed by model name
        for name, info in top_models.items():
            entry = info if isinstance(info, dict) else {}
            entry.setdefault("model", name)
            models_list.append(entry)

    if models_list:
        state["model_benchmarks"] = {
            "timestamp": datetime.now().isoformat(),
            "models": models_list,
        }

    # Track historical best model (90-day rolling window)
    history: list[dict] = state.get("model_benchmark_history", [])
    if models_list:
        best = _pick_best_coding_model(models_list)
        if best:
            history.append({
                "date": datetime.now().strftime("%Y-%m-%d"),
                "model": best.get("model", "unknown"),
                "humaneval_plus": best.get("humaneval_plus"),
                "swe_bench_verified": best.get("swe_bench_verified"),
            })
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
    state["model_benchmark_history"] = [h for h in history if h.get("date", "") >= cutoff]

    return state


def _pick_best_coding_model(models: list[dict]) -> dict | None:
    """Return the model with the highest coding benchmark score."""
    def _score(m: dict) -> float:
        he = m.get("humaneval_plus")
        sw = m.get("swe_bench_verified")
        vals = [v for v in (he, sw) if v is not None]
        return max(vals) if vals else -1
    scored = [(m, _score(m)) for m in models]
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[0][0] if scored and scored[0][1] >= 0 else None


def get_best_model_for_hardware(state: dict, hardware_type: str) -> dict | None:
    """Return the best coding model that fits on the given hardware config."""
    vram_budget = _HARDWARE_VRAM.get(hardware_type)
    if vram_budget is None:
        return None
    models = state.get("model_benchmarks", {}).get("models", [])
    fits = [m for m in models if (m.get("vram_q4") or 0) <= vram_budget and m.get("vram_q4")]
    return _pick_best_coding_model(fits)


def generate_model_comparison_html(state: dict) -> str:
    """Generate an HTML table comparing top models (dark-theme, dashboard-style)."""
    models = state.get("model_benchmarks", {}).get("models", [])
    if not models:
        return '<p style="color:var(--fg,#ccc);">No model benchmark data available yet.</p>'

    def _cell(val):
        if val is None:
            return "—"
        if isinstance(val, float):
            return f"{val:.1f}"
        return str(val)

    # Determine column-best values for highlighting
    def _best(key):
        vals = [m.get(key) for m in models if m.get(key) is not None]
        return max(vals) if vals else None

    best_he = _best("humaneval_plus")
    best_sw = _best("swe_bench_verified")
    best_tok128 = _best("tok_s_128gb_unified")
    best_tok4060 = _best("tok_s_4060ti_moe")

    def _hl(val, best_val):
        if val is not None and best_val is not None and val == best_val:
            return ' style="color:#4caf50;font-weight:bold;"'
        return ""

    rows = []
    for m in models:
        arch = m.get("architecture", "—")
        active = _cell(m.get("active_params_b"))
        he = m.get("humaneval_plus")
        sw = m.get("swe_bench_verified")
        t128 = m.get("tok_s_128gb_unified")
        t4060 = m.get("tok_s_4060ti_moe")
        vram = _cell(m.get("vram_q4"))
        notes = _cell(m.get("notes"))
        rows.append(
            f"<tr>"
            f"<td>{_cell(m.get('model'))}</td>"
            f"<td>{_cell(arch)}</td>"
            f"<td>{active}</td>"
            f"<td{_hl(he, best_he)}>{_cell(he)}</td>"
            f"<td{_hl(sw, best_sw)}>{_cell(sw)}</td>"
            f"<td{_hl(t128, best_tok128)}>{_cell(t128)}</td>"
            f"<td{_hl(t4060, best_tok4060)}>{_cell(t4060)}</td>"
            f"<td>{vram}</td>"
            f"<td>{notes}</td>"
            f"</tr>"
        )

    return (
        '<div class="model-table-wrap">'
        '<table style="width:100%;border-collapse:collapse;background:var(--bg2,#1e1e2e);'
        'color:var(--fg,#cdd6f4);font-size:0.9em;">'
        "<thead><tr>"
        '<th style="padding:8px;border-bottom:2px solid var(--fg,#cdd6f4);text-align:left;">Model</th>'
        '<th style="padding:8px;border-bottom:2px solid var(--fg,#cdd6f4);text-align:left;">Arch</th>'
        '<th style="padding:8px;border-bottom:2px solid var(--fg,#cdd6f4);text-align:right;">Active Params</th>'
        '<th style="padding:8px;border-bottom:2px solid var(--fg,#cdd6f4);text-align:right;">HumanEval+</th>'
        '<th style="padding:8px;border-bottom:2px solid var(--fg,#cdd6f4);text-align:right;">SWE-bench</th>'
        '<th style="padding:8px;border-bottom:2px solid var(--fg,#cdd6f4);text-align:right;">tok/s 128GB</th>'
        '<th style="padding:8px;border-bottom:2px solid var(--fg,#cdd6f4);text-align:right;">tok/s 4060Ti</th>'
        '<th style="padding:8px;border-bottom:2px solid var(--fg,#cdd6f4);text-align:right;">VRAM Q4</th>'
        '<th style="padding:8px;border-bottom:2px solid var(--fg,#cdd6f4);text-align:left;">Notes</th>'
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody>"
        "</table>"
        "</div>"
    )


# ─── Price History Tracking ──────────────────────────────────────────────────

def record_price_history(state: dict, store_results: list, checks: dict) -> dict:
    """Record daily price snapshots for all tracked products.

    Extracts prices from Playwright store_results and Copilot hardware checks,
    appends today's snapshot (deduplicating by date), and trims to a 90-day
    rolling window.  Returns the updated state.
    """
    history = state.setdefault("price_history", {})
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")

    prices: list[tuple[str, float, str]] = []  # (product_key, price, currency)

    # --- Playwright / HTTP store results ---
    if store_results:
        for r in store_results:
            key = r.get("key", "")
            price = r.get("price")
            currency = r.get("currency", "INR")
            if key and price and isinstance(price, (int, float)) and price > 0:
                prices.append((key, float(price), currency))

    # --- Copilot hardware checks ---
    hw = checks.get("hardware", {}) if isinstance(checks, dict) else {}
    if not isinstance(hw, dict):
        hw = {}
    for item_key, item_val in hw.items():
        if not isinstance(item_val, dict):
            continue
        for price_field, currency in (("price_inr", "INR"), ("price_usd", "USD")):
            raw = item_val.get(price_field)
            if raw is None:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            if val > 0:
                product_key = f"{item_key}_{currency.lower()}" if price_field != "price_inr" or currency != "INR" else item_key
                # Normalise to a predictable key: strip trailing _inr/_usd if
                # already present in item_key so we don't double-suffix.
                if item_key.endswith("_india") or item_key.endswith("_inr"):
                    product_key = item_key
                elif item_key.endswith("_us") or item_key.endswith("_usd"):
                    product_key = item_key
                else:
                    product_key = f"{item_key}_{currency.lower()}"
                prices.append((product_key, val, currency))

    # --- Append & deduplicate ---
    for product_key, price, currency in prices:
        entries = history.setdefault(product_key, [])
        # Skip if today already recorded for this product
        if any(e.get("date") == today for e in entries):
            continue
        entries.append({"date": today, "price": price, "currency": currency})
        # Trim to 90-day window
        history[product_key] = [e for e in entries if e.get("date", "") >= cutoff]

    state["price_history"] = history
    return state


def get_price_trend(state: dict, product_key: str, days: int = 30) -> dict:
    """Return price trend summary for a product over the last *days* days.

    Returns a dict with current price, min/max over the window, directional
    trend (up/down/stable/new), percentage change, history slice, and currency.
    """
    history = state.get("price_history", {}).get(product_key, [])
    if not history:
        return {
            "current": None, "min_30d": None, "max_30d": None,
            "trend": "new", "change_pct": 0.0, "history": [], "currency": "INR",
        }

    cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    window = sorted(
        [e for e in history if e.get("date", "") >= cutoff],
        key=lambda e: e["date"],
    )
    if not window:
        window = sorted(history, key=lambda e: e["date"])[-1:]

    prices = [e["price"] for e in window]
    currency = window[-1].get("currency", "INR")
    current = prices[-1]
    min_p = min(prices)
    max_p = max(prices)

    # Trend: compare last-7d average vs prior-7d average
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    fourteen_days_ago = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    recent = [e["price"] for e in window if e["date"] >= seven_days_ago]
    prior = [e["price"] for e in window if fourteen_days_ago <= e["date"] < seven_days_ago]

    if not recent or not prior:
        trend = "new"
        change_pct = 0.0
    else:
        avg_recent = sum(recent) / len(recent)
        avg_prior = sum(prior) / len(prior)
        if avg_prior == 0:
            trend = "new"
            change_pct = 0.0
        else:
            change_pct = round((avg_recent - avg_prior) / avg_prior * 100, 2)
            if change_pct > 2.0:
                trend = "up"
            elif change_pct < -2.0:
                trend = "down"
            else:
                trend = "stable"

    return {
        "current": current,
        "min_30d": min_p,
        "max_30d": max_p,
        "trend": trend,
        "change_pct": change_pct,
        "history": window,
        "currency": currency,
    }


def generate_sparkline_svg(history: list[dict], width: int = 120, height: int = 30) -> str:
    """Generate an inline SVG sparkline from price history entries.

    Each entry is ``{"date": "...", "price": float}``.
    Line colour: green (prices falling), red (rising), gray (stable/insufficient data).
    """
    if not history:
        return (
            f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
            f'<text x="{width // 2}" y="{height // 2 + 4}" text-anchor="middle" '
            f'font-size="10" fill="#999">no data</text></svg>'
        )

    prices = [e["price"] for e in history if isinstance(e.get("price"), (int, float))]
    if not prices:
        return (
            f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
            f'<text x="{width // 2}" y="{height // 2 + 4}" text-anchor="middle" '
            f'font-size="10" fill="#999">no data</text></svg>'
        )

    # Determine trend colour
    if len(prices) < 2:
        colour = "#999"  # gray – single point
    else:
        diff = prices[-1] - prices[0]
        total_range = max(prices) - min(prices) if max(prices) != min(prices) else 1
        pct = abs(diff) / total_range
        if pct < 0.05:
            colour = "#999"   # stable
        elif diff < 0:
            colour = "#22c55e"  # green – prices falling (good)
        else:
            colour = "#ef4444"  # red – prices rising

    padding = 4
    plot_w = width - 2 * padding
    plot_h = height - 2 * padding

    min_p = min(prices)
    max_p = max(prices)
    p_range = max_p - min_p if max_p != min_p else 1.0

    n = len(prices)
    step = plot_w / max(n - 1, 1)
    points = []
    for i, p in enumerate(prices):
        x = round(padding + i * step, 2)
        y = round(padding + plot_h - (p - min_p) / p_range * plot_h, 2)
        points.append(f"{x},{y}")

    last_x, last_y = points[-1].split(",")
    polyline = f'<polyline points="{" ".join(points)}" fill="none" stroke="{colour}" stroke-width="1.5" stroke-linejoin="round"/>'
    dot = f'<circle cx="{last_x}" cy="{last_y}" r="2.5" fill="{colour}"/>'

    return (
        f'<svg width="{width}" height="{height}" xmlns="http://www.w3.org/2000/svg">'
        f'{polyline}{dot}</svg>'
    )


# Store type inference from domain
DOMAIN_STORE_TYPE = {
    "amazon.in": "amazon", "amazon.com": "amazon",
    "apple.com": "apple",
    "flipkart.com": "generic",
}

# Currency inference from domain
DOMAIN_CURRENCY = {
    "amazon.in": "INR", "apple.com/in": "INR", "flipkart.com": "INR",
    "in.store.asus.com": "INR", "croma.com": "INR", "mdcomputers.in": "INR",
    "primeabgb.com": "INR", "elitehubs.com": "INR", "vedantcomputers.com": "INR",
    "theitdepot.com": "INR", "pcstudio.in": "INR", "reliance.digital": "INR",
}


def _extract_domain(url: str) -> str:
    """Extract domain from URL."""
    try:
        from urllib.parse import urlparse
        parsed = urlparse(url)
        return parsed.netloc.lower().replace("www.", "")
    except Exception:
        return ""


def _is_trusted_url(url: str) -> bool:
    """Check if URL is from a trusted store domain."""
    domain = _extract_domain(url)
    return any(trusted in domain for trusted in TRUSTED_STORE_DOMAINS)


def _infer_store_type(url: str) -> str:
    """Infer store type from URL domain."""
    domain = _extract_domain(url)
    for pattern, stype in DOMAIN_STORE_TYPE.items():
        if pattern in domain:
            return stype
    return "generic"


def _infer_currency(url: str) -> str:
    """Infer currency from URL domain/path."""
    full = url.lower()
    for pattern, cur in DOMAIN_CURRENCY.items():
        if pattern in full:
            return cur
    return "USD"


def _normalize_key(name: str) -> str:
    """Generate a canonical key from a product name."""
    key = re.sub(r'[^a-z0-9]+', '_', name.lower().strip())
    key = re.sub(r'_+', '_', key).strip('_')
    return key[:60]


def _get_static_keys() -> set:
    """Get all keys from the hardcoded STORE_CHECK_CONFIGS."""
    return {c["key"] for c in STORE_CHECK_CONFIGS}


def _is_relevant_to_goal(info_text: str) -> bool:
    """Check if a product mention is relevant to user's LLM inference/fine-tuning goal.

    Must match at least one RELEVANCE_KEYWORD and NOT match any ruled-out items.
    """
    text_lower = info_text.lower()
    # Reject ruled-out items
    for item in USER_CONSTRAINTS.get("ruled_out", []):
        # Convert ruled_out keys to search-friendly forms
        searchable = item.replace("_", " ").replace("-", " ")
        if searchable in text_lower:
            return False
    return any(kw in text_lower for kw in RELEVANCE_KEYWORDS)


def load_dynamic_stores(state: dict) -> list[dict]:
    """Load dynamic store configs from state, returning only 'validated' or 'tracked' entries."""
    dynamic = state.get("dynamic_stores", {})
    stores = dynamic.get("stores", [])
    return [s for s in stores if s.get("status") in ("validated", "tracked")]


def get_all_store_configs(state: dict) -> list[dict]:
    """Merge static + dynamic store configs, deduplicating by key."""
    static_keys = _get_static_keys()
    all_configs = list(STORE_CHECK_CONFIGS)

    for ds in load_dynamic_stores(state):
        if ds["key"] not in static_keys:
            # Convert dynamic entry to store config format
            all_configs.append({
                "key": ds["key"],
                "label": ds["label"],
                "url": ds["url"],
                "store": ds.get("store", "generic"),
                "currency": ds.get("currency", "USD"),
                "search_terms": ds.get("search_terms", []),
                "_dynamic": True,
            })

    return all_configs


def extract_discoveries_from_results(checks: dict, state: dict) -> list[dict]:
    """Extract potential new hardware from monitoring results (no extra API call).

    Scans all check results for product mentions with URLs that could be
    new store entries. Also processes structured items from
    new_hardware_discoveries.items and new_products_from_blogs.
    Returns candidate entries for validation.
    """
    static_keys = _get_static_keys()
    existing_dynamic = {s["key"] for s in state.get("dynamic_stores", {}).get("stores", [])}
    pruned_keys = {p["key"] for p in state.get("dynamic_stores", {}).get("pruned", [])}
    candidates = []
    seen_urls = set()

    def _add_candidate(url: str, label: str, source: str, info: str):
        """Helper to add a candidate if it passes all filters."""
        url = url.rstrip('.,;:)]}')
        if url in seen_urls or not _is_trusted_url(url):
            return
        seen_urls.add(url)

        domain = _extract_domain(url)
        candidate_key = _normalize_key(f"{domain}_{label.replace(' ', '_')[:30]}")

        if candidate_key in static_keys or candidate_key in existing_dynamic:
            return
        if candidate_key in pruned_keys:
            return

        candidates.append({
            "key": candidate_key,
            "label": f"Discovered: {label} ({domain})",
            "url": url,
            "store": _infer_store_type(url),
            "currency": _infer_currency(url),
            "status": "candidate",
            "discovered_from": source,
            "discovered_date": datetime.now().strftime("%Y-%m-%d"),
            "last_checked": None,
            "consecutive_failures": 0,
            "failure_types": [],
            "successful_checks": 0,
            "provenance": info[:200],
        })

    # 1. Scan all categories for URLs in info text (original approach)
    for category, items in checks.items():
        if not isinstance(items, dict):
            continue
        for key, data in items.items():
            if not isinstance(data, dict):
                continue

            info = str(data.get("info", ""))

            # Only consider items relevant to our LLM inference/fine-tuning goal
            if not _is_relevant_to_goal(info):
                continue

            # Extract URLs from info text
            urls = re.findall(r'https?://[^\s<>"\']+', info)
            for url in urls:
                _add_candidate(url, key, f"{category}.{key}", info)

    # 2. Process structured discovery arrays (new_hardware_discoveries.items, new_products_from_blogs)
    for discovery_key in ("new_hardware_discoveries", "new_products_from_blogs"):
        for category, items in checks.items():
            if not isinstance(items, dict):
                continue
            disc_data = items.get(discovery_key, {})
            if not isinstance(disc_data, dict):
                continue

            # Process structured items array if present
            disc_items = disc_data.get("items", [])
            if isinstance(disc_items, list):
                for item in disc_items:
                    if not isinstance(item, dict):
                        continue
                    url = item.get("url", "")
                    name = item.get("name", item.get("product", "unknown"))
                    if url and _is_relevant_to_goal(name + " " + str(item.get("memory_gb", ""))):
                        _add_candidate(url, name, f"{category}.{discovery_key}", json.dumps(item)[:200])

            # Also scan the info text for URLs (in case Copilot put URLs there)
            info = str(disc_data.get("info", ""))
            if info and _is_relevant_to_goal(info):
                urls = re.findall(r'https?://[^\s<>"\']+', info)
                for url in urls:
                    _add_candidate(url, discovery_key, f"{category}.{discovery_key}", info)

    return candidates


def update_dynamic_stores(state: dict, store_results: list[dict]) -> dict:
    """Update dynamic store statuses based on check results.

    Lifecycle: candidate → validated (1 success) → tracked (2+ successes)
                          → quarantined (5+ strong failures) → pruned (10+ or 14 days)
    """
    dynamic = state.setdefault("dynamic_stores", {"stores": [], "pruned": []})
    stores = dynamic["stores"]
    today = datetime.now().strftime("%Y-%m-%d")

    # Build lookup of results by key
    result_by_key = {r["key"]: r for r in store_results}

    for entry in stores:
        result = result_by_key.get(entry["key"])
        if not result:
            continue

        entry["last_checked"] = today
        status = result.get("playwright_status", "")
        signal = result.get("availability_signal", "unreachable")

        # Classify failure type
        is_strong_failure = any(x in status for x in ("404", "410", "content_gone"))
        is_soft_failure = signal == "unreachable" and not is_strong_failure
        is_success = signal != "unreachable"

        if is_success:
            entry["consecutive_failures"] = 0
            entry["failure_types"] = []
            entry["successful_checks"] = entry.get("successful_checks", 0) + 1

            # Promote: candidate → validated → tracked
            if entry["status"] == "candidate" and entry["successful_checks"] >= 1:
                entry["status"] = "validated"
                logger.info(f"Dynamic store promoted to validated: {entry['key']}")
            elif entry["status"] == "validated" and entry["successful_checks"] >= 2:
                entry["status"] = "tracked"
                logger.info(f"Dynamic store promoted to tracked: {entry['key']}")

            # Update label with real data if available
            if result.get("price") and "Discovered:" in entry.get("label", ""):
                price = result["price"]
                cur = result.get("currency", entry.get("currency", "USD"))
                entry["label"] = entry["label"].replace("Discovered: ", "")
                if cur == "INR":
                    try:
                        entry["label"] += f" ~₹{float(price):,.0f}"
                    except (ValueError, TypeError):
                        entry["label"] += f" ~₹{price}"
                else:
                    try:
                        entry["label"] += f" ~${float(price):,.0f}"
                    except (ValueError, TypeError):
                        entry["label"] += f" ~${price}"

        elif is_strong_failure:
            entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1
            ft = entry.setdefault("failure_types", [])
            ft.append(status[:50])
            ft[:] = ft[-5:]  # keep last 5

            if entry["consecutive_failures"] >= 5 and entry["status"] != "quarantined":
                entry["status"] = "quarantined"
                entry["quarantined_date"] = today
                logger.info(f"Dynamic store quarantined: {entry['key']} ({status})")

        elif is_soft_failure:
            entry["consecutive_failures"] = entry.get("consecutive_failures", 0) + 1

    # Prune quarantined items after 10+ failures or 14 days in quarantine
    still_active = []
    for entry in stores:
        if entry.get("status") == "quarantined":
            q_date = entry.get("quarantined_date", today)
            days_quarantined = (datetime.now() - datetime.strptime(q_date, "%Y-%m-%d")).days
            if entry.get("consecutive_failures", 0) >= 10 or days_quarantined >= 14:
                dynamic["pruned"].append({
                    "key": entry["key"],
                    "url": entry["url"],
                    "reason": f"failures={entry.get('consecutive_failures')}, days={days_quarantined}",
                    "pruned_date": today,
                })
                logger.info(f"Dynamic store pruned: {entry['key']}")
                continue
        still_active.append(entry)

    dynamic["stores"] = still_active

    # Cap pruned list to last 50
    dynamic["pruned"] = dynamic["pruned"][-50:]

    return state


def build_dynamic_prompt_context(state: dict, category: str = "hardware") -> str:
    """Build a context snippet for prompts based on known facts and discoveries.

    For 'hardware': injects confirmed prices/availability so Copilot focuses on unknowns.
    For 'models_and_agents': injects latest model discoveries so Copilot looks for newer stuff.
    For 'deals_and_blogs': injects known prices as baseline so Copilot finds actual deals.
    """
    parts = []

    # --- Store check facts (prices, availability) ---
    store_check = state.get("store_check", {})
    results = store_check.get("results", [])
    timestamp = store_check.get("timestamp", "")
    age_days = 999
    if timestamp:
        try:
            check_date = datetime.fromisoformat(timestamp)
            age_days = (datetime.now() - check_date).days
        except (ValueError, TypeError):
            pass

    if age_days <= 7 and results:
        known_facts = []
        for r in results:
            if r.get("availability_signal") in ("unreachable",):
                continue
            price = r.get("price")
            cur = r.get("currency", "USD")
            sig = r.get("availability_signal", "unknown")
            label = r.get("label", r.get("key", "?"))

            if price:
                try:
                    ps = f"₹{float(price):,.0f}" if cur == "INR" else f"${float(price):,.0f}"
                except (ValueError, TypeError):
                    ps = f"{cur} {price}"
                known_facts.append(f"{label}: {ps} ({sig})")
            elif sig != "unreachable":
                known_facts.append(f"{label}: ({sig})")

        if known_facts:
            parts.append(
                f"CONFIRMED PRICES ({age_days}d ago): " +
                "; ".join(known_facts[:12])
            )

    # --- Dynamic discoveries ---
    dynamic = state.get("dynamic_stores", {})
    dyn_stores = dynamic.get("stores", [])
    tracked = [s for s in dyn_stores if s.get("status") in ("validated", "tracked")]
    if tracked:
        disc_items = []
        for s in tracked[:8]:
            label = s.get("label", s.get("key", "?"))
            url = s.get("url", "")
            disc_items.append(f"{label} ({url[:60]})" if url else label)
        parts.append("PREVIOUSLY DISCOVERED PRODUCTS (check for updates): " + "; ".join(disc_items))

    # Also include recent candidates that haven't been validated yet — mention for awareness
    candidates = [s for s in dyn_stores if s.get("status") == "candidate"]
    if candidates:
        cand_names = [s.get("label", s.get("key", "?")) for s in candidates[:3]]
        parts.append("UNVERIFIED LEADS: " + "; ".join(cand_names))

    # --- Category-specific context ---
    checks = state.get("checks", {})

    if category == "hardware":
        if parts:
            parts.append("VERIFY these are still current. Focus on items NOT listed or that may have CHANGED.")

    elif category == "models_and_agents":
        # Inject last known model findings so Copilot looks for NEWER stuff
        models_data = checks.get("models_and_agents", {})
        model_facts = []
        for key in ("best_local_coding_models", "fine_tuning_models", "inference_runtimes", "coding_agent_frameworks"):
            item = models_data.get(key, {})
            info = item.get("info", "")[:100]
            if info:
                model_facts.append(f"{key}: {info}")
        if model_facts:
            parts.append(
                "LAST KNOWN: " + "; ".join(model_facts) +
                ". Search for anything NEWER than these."
            )

    elif category == "efficiency_research":
        # Inject previous efficiency findings so Copilot focuses on what's NEW
        efficiency_data = checks.get("efficiency_research", {})
        eff_facts = []
        for key in ("quantization_breakthroughs", "inference_engine_updates",
                     "moe_offloading", "budget_gpu_benchmarks",
                     "efficient_model_architectures", "memory_optimization",
                     "community_discoveries"):
            item = efficiency_data.get(key, {})
            info = item.get("info", "")[:100]
            if info:
                eff_facts.append(f"{key}: {info}")
        if eff_facts:
            parts.append("PREVIOUS FINDINGS: " + "; ".join(eff_facts))
        parts.append(
            "Current baseline: Qwen3-30B-A3B at 30-45 tok/s on RTX 4060 Ti 16GB "
            "with --n-cpu-moe expert offloading. KTransformers achieves 14-20 tok/s "
            "on weaker hardware. llama.cpp MoE flags: -ncmoe 99 -fa on -ctk q4_0 -ctv q4_0"
        )
        parts.append("Focus on what is NEW since last check.")

    elif category == "deals_and_blogs":
        # Inject known prices so Copilot can identify actual deals vs normal prices
        if parts:  # reuse CONFIRMED PRICES from above
            parts.append("Only report deals that are BELOW these confirmed prices or genuinely new offers.")

    if not parts:
        return ""

    return " ".join(parts)


async def _check_apple_store_page(page, config: dict) -> dict:
    """Apple Store-specific checks: schema.org JSON, configurator radio buttons."""
    result = _make_result_template(config)

    try:
        resp = await page.goto(config["url"], wait_until="networkidle", timeout=45000)
        await page.wait_for_timeout(4000)

        if not resp or resp.status >= 400:
            result["playwright_status"] = "http_error"
            return result

        result["page_reachable"] = True

        # Extract schema.org JSON for price/SKU
        html_content = await page.content()
        ld_scripts = re.findall(
            r'<script\s+type="application/ld\+json">\s*([\s\S]*?)</script>',
            html_content
        )
        for script_text in ld_scripts:
            try:
                schema = json.loads(script_text.strip())
                if schema.get("@type") == "Product" and "offers" in schema:
                    offers = schema["offers"]
                    if isinstance(offers, list) and offers:
                        result["price"] = offers[0].get("price")
                        result["sku_present"] = bool(offers[0].get("sku"))
                    elif isinstance(offers, dict):
                        result["price"] = offers.get("price")
                        result["sku_present"] = bool(offers.get("sku"))
                    break
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        # Check configurator radio buttons
        config_check = await page.evaluate("""() => {
            const radios = document.querySelectorAll('input[type=radio]');
            let has16core = false, has128gb = false, core16checked = false, mem128checked = false;
            for (const r of radios) {
                const val = r.value || '';
                const name = r.name || '';
                if (val.includes('16-40') || (name.includes('cpuCoreCount') && val.includes('16'))) {
                    has16core = true;
                    if (r.checked) core16checked = true;
                }
                if (val.includes('128gb') || (name.includes('Memory') && val.includes('128'))) {
                    has128gb = true;
                    if (r.checked) mem128checked = true;
                }
            }
            const btns = document.querySelectorAll('button');
            let continueEnabled = false;
            for (const b of btns) {
                if (b.textContent.trim() === 'Continue' && b.offsetParent !== null) {
                    continueEnabled = !b.disabled && b.getAttribute('aria-disabled') !== 'true';
                    break;
                }
            }
            return {has16core, has128gb, core16checked, mem128checked, continueEnabled};
        }""")

        result["target_config_selectable"] = (
            config_check.get("has16core", False) and
            config_check.get("has128gb", False)
        )
        result["continue_enabled"] = config_check.get("continueEnabled", False)

        # Try clicking 16-core if not checked
        if config_check.get("has16core") and not config_check.get("core16checked"):
            await page.evaluate("""() => {
                const radios = document.querySelectorAll('input[type=radio]');
                for (const r of radios) {
                    const val = r.value || '';
                    if (val.includes('16-40') || (r.name.includes('cpuCoreCount') && val.includes('16'))) {
                        const label = r.closest('label') || r.parentElement;
                        if (label) label.click(); else r.click();
                        break;
                    }
                }
            }""")
            await page.wait_for_timeout(2000)
            cont_state = await page.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    if (b.textContent.trim() === 'Continue' && b.offsetParent !== null)
                        return !b.disabled;
                }
                return false;
            }""")
            result["continue_enabled"] = cont_state

        # Out-of-stock and delivery checks
        body = await page.inner_text("body")
        _check_body_signals(result, body)

        # Signal level
        if result["availability_signal"] != "out_of_stock":
            if result["continue_enabled"]:
                result["availability_signal"] = "config_validated"
            elif result["target_config_selectable"] and result["sku_present"]:
                result["availability_signal"] = "metadata_only"
            elif result["page_reachable"] and result["sku_present"]:
                result["availability_signal"] = "metadata_only"
            else:
                result["availability_signal"] = "page_only"

        result["playwright_status"] = "ok"

    except Exception as e:
        result["playwright_status"] = f"error: {str(e)[:100]}"
        logger.warning(f"Playwright check failed for {config['label']}: {e}")

    return result


async def _check_amazon_page(page, config: dict) -> dict:
    """Amazon product page: price, in-stock, delivery, buy-box."""
    result = _make_result_template(config)

    try:
        resp = await page.goto(config["url"], wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(3000)

        if not resp or resp.status >= 400:
            result["playwright_status"] = "http_error"
            return result

        result["page_reachable"] = True

        # Extract Amazon data via JS
        amazon_data = await page.evaluate("""() => {
            const title = document.getElementById('productTitle');
            const price = document.querySelector('.a-price .a-offscreen, #priceblock_ourprice, #priceblock_dealprice, .reinventPricePriceToPayMargin .a-offscreen');
            const avail = document.getElementById('availability');
            const buyBox = document.getElementById('add-to-cart-button');
            const delivery = document.querySelector('#deliveryBlockMessage, [data-csa-c-delivery-price], .a-text-bold[data-csa-c-type="element"]');
            const outOfStock = document.querySelector('#outOfStock, .a-color-price.a-text-bold');

            return {
                title: title ? title.textContent.trim() : null,
                price: price ? price.textContent.trim() : null,
                availability: avail ? avail.textContent.trim() : null,
                hasBuyBox: !!buyBox && !buyBox.disabled,
                deliveryText: delivery ? delivery.textContent.trim().substring(0, 100) : null,
                hasOutOfStock: !!outOfStock && outOfStock.textContent.toLowerCase().includes('unavailable'),
            };
        }""")

        if amazon_data.get("price"):
            price_str = re.sub(r'[^\d.]', '', amazon_data["price"].replace(",", ""))
            try:
                result["price"] = float(price_str)
            except ValueError:
                pass

        result["sku_present"] = bool(amazon_data.get("title"))

        body = await page.inner_text("body")
        _check_body_signals(result, body, config.get("out_of_stock_phrases"))

        if amazon_data.get("hasOutOfStock"):
            result["availability_signal"] = "out_of_stock"
        elif amazon_data.get("hasBuyBox"):
            result["availability_signal"] = "add_to_cart"
            result["target_config_selectable"] = True
        elif result["price"]:
            result["availability_signal"] = "metadata_only"
        else:
            result["availability_signal"] = "page_only"

        if amazon_data.get("deliveryText"):
            result["delivery_info"] = amazon_data["deliveryText"][:100]

        result["playwright_status"] = "ok"

    except Exception as e:
        result["playwright_status"] = f"error: {str(e)[:100]}"
        logger.warning(f"Playwright check failed for {config['label']}: {e}")

    return result


async def _check_generic_page(page, config: dict) -> dict:
    """Generic store page: look for price, add-to-cart, stock signals."""
    result = _make_result_template(config)

    try:
        resp = await page.goto(config["url"], wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(4000)

        if not resp or resp.status >= 400:
            result["playwright_status"] = "http_error"
            return result

        result["page_reachable"] = True

        html_content = await page.content()
        body = await page.inner_text("body")

        # Try schema.org for price
        ld_scripts = re.findall(
            r'<script\s+type="application/ld\+json">\s*([\s\S]*?)</script>',
            html_content
        )
        for script_text in ld_scripts:
            try:
                schema = json.loads(script_text.strip())
                target = schema
                if isinstance(schema, list):
                    target = next((s for s in schema if s.get("@type") == "Product"), None)
                if target and target.get("@type") == "Product":
                    offers = target.get("offers", {})
                    if isinstance(offers, list) and offers:
                        offers = offers[0]
                    if isinstance(offers, dict):
                        result["price"] = offers.get("price")
                        result["sku_present"] = True
                        avail = offers.get("availability", "")
                        if "InStock" in avail:
                            result["availability_signal"] = "in_stock_schema"
                        elif "OutOfStock" in avail:
                            result["availability_signal"] = "out_of_stock"
                    break
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        # Fallback: search for price patterns in body text
        if result["price"] is None:
            currency = config.get("currency", "USD")
            if currency == "INR":
                price_match = re.search(r'[\u20B9₹]\s*([\d,]+(?:\.\d{2})?)', body)
            else:
                price_match = re.search(r'\$\s*([\d,]+(?:\.\d{2})?)', body)
            if price_match:
                try:
                    result["price"] = float(price_match.group(1).replace(",", ""))
                except ValueError:
                    pass

        # Check buy/cart buttons
        buy_data = await page.evaluate("""() => {
            const btns = document.querySelectorAll('button, a.btn, [class*=add-to-cart], [class*=buy-now], [class*=addToCart]');
            let hasBuy = false;
            for (const b of btns) {
                const txt = (b.textContent || '').toLowerCase().trim();
                if ((txt.includes('add to cart') || txt.includes('buy now') || txt.includes('add to bag')
                     || txt.includes('pre-order') || txt.includes('order now')) && b.offsetParent !== null) {
                    hasBuy = !b.disabled;
                    break;
                }
            }
            return {hasBuy};
        }""")

        # Out-of-stock and delivery
        _check_body_signals(result, body, config.get("out_of_stock_phrases"))

        if result["availability_signal"] == "out_of_stock":
            pass  # already set
        elif result.get("availability_signal") == "in_stock_schema":
            result["target_config_selectable"] = True
        elif buy_data.get("hasBuy"):
            result["availability_signal"] = "add_to_cart"
            result["target_config_selectable"] = True
        elif result["price"]:
            result["availability_signal"] = "metadata_only"
        elif result["page_reachable"]:
            result["availability_signal"] = "page_only"

        result["playwright_status"] = "ok"

    except Exception as e:
        result["playwright_status"] = f"error: {str(e)[:100]}"
        logger.warning(f"Playwright check failed for {config['label']}: {e}")

    return result


def _make_result_template(config: dict) -> dict:
    """Create a blank result dict for a store check."""
    result = {
        "key": config["key"],
        "label": config["label"],
        "url": config["url"],
        "page_reachable": False,
        "sku_present": False,
        "price": None,
        "currency": config.get("currency", "USD"),
        "target_config_selectable": False,
        "continue_enabled": False,
        "availability_signal": "unreachable",
        "playwright_status": "pending",
        "delivery_info": None,
    }
    if config.get("_dynamic"):
        result["_dynamic"] = True
    return result


def _check_body_signals(result: dict, body: str, extra_oos_phrases: list = None):
    """Check body text for out-of-stock and delivery signals."""
    oos_phrases = ["Currently Unavailable", "Out of Stock", "Sold Out",
                   "not currently available", "Temporarily unavailable"]
    if extra_oos_phrases:
        oos_phrases.extend(extra_oos_phrases)

    body_lower = body.lower()
    for phrase in oos_phrases:
        if phrase.lower() in body_lower:
            result["availability_signal"] = "out_of_stock"
            break

    if not result.get("delivery_info"):
        for kw in ["Delivers", "Get it by", "Ships by", "delivery by", "FREE Delivery",
                    "Estimated delivery", "Expected delivery"]:
            idx = body.find(kw)
            if idx >= 0:
                result["delivery_info"] = body[idx:idx+80].replace("\n", " ").strip()
                break


# ─── Fallback: Simple HTTP check (no Playwright) ────────────────────────────

# User-agent rotation for HTTP fallback
_FALLBACK_USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:128.0) Gecko/20100101 Firefox/128.0",
]


def _http_fallback_check(config: dict, ua_index: int = 0) -> dict:
    """Lightweight HTTP GET fallback for when Playwright fails on a store.

    Parses schema.org JSON-LD and basic HTML signals from raw HTML.
    Won't work for JS-rendered pages (Apple configurator) but can catch
    schema.org metadata, price patterns, and out-of-stock keywords.
    """
    result = _make_result_template(config)
    result["fallback_used"] = "http"

    ua = _FALLBACK_USER_AGENTS[ua_index % len(_FALLBACK_USER_AGENTS)]
    headers = {
        "User-Agent": ua,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "identity",
        "Connection": "keep-alive",
        "Cache-Control": "no-cache",
    }

    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(config["url"], headers=headers)
        with urllib.request.urlopen(req, timeout=20, context=ctx) as resp:
            if resp.status >= 400:
                result["playwright_status"] = "http_fallback_error"
                return result

            raw = resp.read(500_000)  # limit to 500KB
            html = raw.decode("utf-8", errors="replace")

        result["page_reachable"] = True

        # Try schema.org JSON-LD
        ld_scripts = re.findall(
            r'<script\s+type="application/ld\+json">\s*([\s\S]*?)</script>', html
        )
        for script_text in ld_scripts:
            try:
                schema = json.loads(script_text.strip())
                target = schema
                if isinstance(schema, list):
                    target = next((s for s in schema if s.get("@type") == "Product"), None)
                if target and target.get("@type") == "Product":
                    offers = target.get("offers", {})
                    if isinstance(offers, list) and offers:
                        offers = offers[0]
                    if isinstance(offers, dict):
                        result["price"] = offers.get("price")
                        result["sku_present"] = True
                        avail = offers.get("availability", "")
                        if "InStock" in avail:
                            result["availability_signal"] = "in_stock_schema"
                        elif "OutOfStock" in avail:
                            result["availability_signal"] = "out_of_stock"
                    break
            except (json.JSONDecodeError, KeyError, TypeError):
                continue

        # Fallback price regex from HTML
        if result["price"] is None:
            currency = config.get("currency", "USD")
            if currency == "INR":
                price_match = re.search(r'[\u20B9₹]\s*([\d,]+(?:\.\d{2})?)', html)
            else:
                price_match = re.search(r'\$\s*([\d,]+(?:\.\d{2})?)', html)
            if price_match:
                try:
                    result["price"] = float(price_match.group(1).replace(",", ""))
                except ValueError:
                    pass

        # Strip HTML tags for body text analysis
        body_text = re.sub(r'<[^>]+>', ' ', html)
        body_text = re.sub(r'\s+', ' ', body_text)

        _check_body_signals(result, body_text, config.get("out_of_stock_phrases"))

        # Determine signal if not already set by out_of_stock
        if result["availability_signal"] not in ("out_of_stock", "in_stock_schema"):
            if result["price"] and result["sku_present"]:
                result["availability_signal"] = "metadata_only"
            elif result["page_reachable"]:
                result["availability_signal"] = "page_only"

        result["playwright_status"] = "http_fallback_ok"

    except urllib.error.HTTPError as e:
        result["playwright_status"] = f"http_fallback_error_{e.code}"
        logger.debug(f"HTTP fallback {e.code} for {config['label']}: {e.reason}")
    except Exception as e:
        result["playwright_status"] = f"http_fallback_error"
        logger.debug(f"HTTP fallback failed for {config['label']}: {e}")

    return result


def _get_stale_cache_result(config: dict, state: dict) -> dict | None:
    """Return the last successful result for this store from state, marked stale."""
    prev = state.get("store_check", {}).get("results", [])
    for r in prev:
        if r.get("key") == config["key"] and r.get("playwright_status", "").startswith(("ok", "http_fallback_ok")):
            stale = dict(r)
            stale["fallback_used"] = "stale_cache"
            stale["stale_from"] = state.get("store_check", {}).get("timestamp", "unknown")
            stale["playwright_status"] = "stale_cache"
            return stale
    return None


async def _check_all_stores(state: dict = None) -> list[dict]:
    """Check all store pages with retry, HTTP fallback, and stale cache.

    Fallback chain per store:
      1. Playwright with primary context
      2. Playwright retry with alternate user-agent + stealth headers
      3. Simple HTTP GET (urllib) — works for non-JS pages
      4. Stale cache from last successful run
    """
    state = state or {}
    results = []

    # Alternate stealth context settings for retry
    _STEALTH_UA = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.1 Safari/605.1.15"
    )

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        # Primary context
        ctx_primary = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={"width": 1440, "height": 900},
            locale="en-IN",
        )
        page_primary = await ctx_primary.new_page()

        # Stealth context (different UA, locale, viewport) for retries
        ctx_stealth = await browser.new_context(
            user_agent=_STEALTH_UA,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            extra_http_headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
            },
        )
        page_stealth = await ctx_stealth.new_page()

        # Use merged static + dynamic configs
        store_configs = get_all_store_configs(state)
        logger.info(f"Checking {len(store_configs)} stores ({len(STORE_CHECK_CONFIGS)} static + {len(store_configs) - len(STORE_CHECK_CONFIGS)} dynamic)")

        for idx, config in enumerate(store_configs):
            label = config["label"]
            logger.info(f"Playwright: checking {label}...")

            store_type = config.get("store", "generic")
            handler = {
                "apple": _check_apple_store_page,
                "amazon": _check_amazon_page,
            }.get(store_type, _check_generic_page)

            # ── Attempt 1: Playwright primary context ──
            result = await handler(page_primary, config)

            # ── Attempt 2: Playwright stealth context (on failure) ──
            if result["playwright_status"] not in ("ok",):
                logger.info(f"  ↻ Retry with stealth context for {label}...")
                result = await handler(page_stealth, config)
                if result["playwright_status"] == "ok":
                    result["fallback_used"] = "stealth_retry"

            # ── Attempt 3: Simple HTTP fallback (on failure) ──
            if result["playwright_status"] not in ("ok",):
                logger.info(f"  ↻ HTTP fallback for {label}...")
                result = _http_fallback_check(config, ua_index=idx)

            # ── Attempt 4: Stale cache (on total failure) ──
            if result["availability_signal"] == "unreachable":
                cached = _get_stale_cache_result(config, state)
                if cached:
                    logger.info(f"  ↻ Using stale cache from {cached.get('stale_from', '?')} for {label}")
                    result = cached

            results.append(result)
            fallback_tag = f" [fallback={result.get('fallback_used', 'none')}]" if result.get("fallback_used") else ""
            logger.info(
                f"  → signal={result['availability_signal']} "
                f"price={result['price']} "
                f"status={result['playwright_status']}{fallback_tag}"
            )

        await browser.close()

    return results


def _tag_freshness(results: list[dict]) -> list[dict]:
    """Tag each store result with a freshness level.

    - 'fresh_verified': Playwright primary or stealth success
    - 'partial': HTTP-only fallback (no JS rendering)
    - 'stale': from cache, older data
    """
    for r in results:
        fallback = r.get("fallback_used")
        if fallback == "stale_cache":
            r["freshness"] = "stale"
            # Compute stale age
            stale_from = r.get("stale_from", "")
            if stale_from and stale_from != "unknown":
                try:
                    stale_dt = datetime.fromisoformat(stale_from)
                    r["stale_hours"] = round((datetime.now() - stale_dt).total_seconds() / 3600, 1)
                except (ValueError, TypeError):
                    r["stale_hours"] = None
        elif fallback == "http":
            r["freshness"] = "partial"
        else:
            # Primary playwright or stealth retry — check if actually successful
            if r.get("playwright_status", "").startswith("ok"):
                r["freshness"] = "fresh_verified"
            elif r.get("page_reachable"):
                r["freshness"] = "partial"
            else:
                r["freshness"] = "stale" if r.get("availability_signal") != "unreachable" else "unknown"
        r["checked_at"] = datetime.now().isoformat()
    return results


def check_store_availability(state: dict = None) -> list[dict]:
    """Sync wrapper for all store availability checks.

    Fallback chain: Playwright → Playwright stealth retry → HTTP GET → stale cache.
    Returns list of result dicts, or empty list if Playwright unavailable.
    Each result gets a 'freshness' field: 'fresh_verified', 'partial', or 'stale'.
    """
    if not HAS_PLAYWRIGHT:
        logger.warning(
            "Playwright not installed — falling back to HTTP-only store checks. "
            "Install with: pip install playwright && python -m playwright install chromium"
        )
        # Even without Playwright, try HTTP fallback for all stores
        all_configs = get_all_store_configs(state or {})
        results = []
        for idx, config in enumerate(all_configs):
            logger.info(f"HTTP check: {config['label']}...")
            result = _http_fallback_check(config, ua_index=idx)
            if result["availability_signal"] == "unreachable" and state:
                cached = _get_stale_cache_result(config, state)
                if cached:
                    result = cached
            results.append(result)
        return _tag_freshness(results)

    try:
        return _tag_freshness(asyncio.run(_check_all_stores(state=state or {})))
    except Exception as e:
        logger.error(f"Store availability check failed: {e}")
        # Last resort: HTTP fallback for everything
        logger.info("Attempting HTTP-only fallback for all stores...")
        all_configs = get_all_store_configs(state or {})
        results = []
        for idx, config in enumerate(all_configs):
            result = _http_fallback_check(config, ua_index=idx)
            if result["availability_signal"] == "unreachable" and state:
                cached = _get_stale_cache_result(config, state or {})
                if cached:
                    result = cached
            results.append(result)
        return _tag_freshness(results)


def merge_playwright_results(checks: dict, store_results: list[dict]) -> dict:
    """Merge Playwright store results into the check data across categories."""
    if not store_results:
        return checks

    # Map from store result key → which category + check key it maps to
    key_to_category = {
        # Mac Studio (highest priority)
        "mac_studio_128gb_india": ("hardware", "mac_studio_128gb_india"),
        "mac_studio_128gb_india_1tb": ("hardware", "mac_studio_128gb_india"),  # merge as alt
        "mac_studio_128gb_us": ("hardware", "mac_studio_128gb_us"),
        # Mac Mini (budget option)
        "mac_mini_48gb_india": ("hardware", "mac_mini_48gb_india"),
        "mac_mini_48gb_us": ("hardware", "mac_mini_48gb_us"),
        # Apple Refurbished
        "apple_refurbished_india": ("hardware", "apple_refurbished"),
        "apple_refurbished_us": ("hardware", "apple_refurbished"),
        # Strix Halo mini PCs
        "framework_desktop_128gb": ("hardware", "framework_desktop_128gb"),
        "bosgame_m5_128gb": ("hardware", "bosgame_m5_128gb"),
        "beelink_gtr9_pro_128gb": ("hardware", "beelink_gtr9_pro_128gb"),
        "minisforum_ms_s1_max": ("hardware", "minisforum_ms_s1_max"),
        "corsair_ws300": ("hardware", "corsair_ws300"),
        "amd_strix_halo_128gb_india": ("hardware", "amd_strix_halo_128gb_india"),
        # GPUs
        "rtx_5090_india": ("hardware", "rtx_5090_india"),
        "rtx_5090_flipkart": ("hardware", "rtx_5090_india"),  # merge as alt
    }

    for result in store_results:
        mapping = key_to_category.get(result["key"])
        if mapping:
            category, check_key = mapping
            if category not in checks or check_key not in checks[category]:
                continue

            existing = checks[category][check_key]
            if not isinstance(existing, dict):
                continue

            is_primary = result["key"] == check_key
            is_alt = not is_primary

            if is_primary:
                # Primary match — overlay verified fields
                existing["playwright_verified"] = result["playwright_status"] == "ok"
                existing["availability_signal"] = result["availability_signal"]
                existing["playwright_status"] = result["playwright_status"]
                existing["store_url"] = result["url"]
                existing["freshness"] = result.get("freshness", "unknown")
                if result.get("stale_hours"):
                    existing["stale_hours"] = result["stale_hours"]
                existing["checked_at"] = result.get("checked_at", "")

                if result["price"] is not None:
                    price_key = "price_inr" if result["currency"] == "INR" else "price_usd"
                    existing[price_key] = result["price"]

                if result["delivery_info"]:
                    existing["delivery_info"] = result["delivery_info"]

                if result["availability_signal"] == "out_of_stock":
                    existing["orderable"] = False
                    existing["in_stock"] = False
                elif result["availability_signal"] in ("config_validated", "add_to_cart", "in_stock_schema"):
                    existing["orderable"] = True

            else:
                # Alternative source — store as extra data
                alt_key = f"alt_{result['key']}"
                existing[alt_key] = {
                    "label": result["label"],
                    "url": result["url"],
                    "signal": result["availability_signal"],
                    "price": result["price"],
                    "currency": result["currency"],
                    "delivery": result.get("delivery_info"),
                }

        elif result.get("_dynamic"):
            # Dynamic discovery — inject into hardware checks so it appears on dashboard
            hw = checks.setdefault("hardware", {})
            rkey = result["key"]
            if rkey not in hw:
                sig = result.get("availability_signal", "unknown")
                price = result.get("price")
                cur = result.get("currency", "USD")
                try:
                    price_str = f"₹{float(price):,.0f}" if cur == "INR" and price else (f"${float(price):,.0f}" if price else "unknown")
                except (ValueError, TypeError):
                    price_str = f"{cur} {price}" if price else "unknown"
                hw[rkey] = {
                    "available": sig in ("config_validated", "add_to_cart", "in_stock_schema"),
                    "info": f"[Discovered] {result.get('label', rkey)} — {sig}, {price_str}",
                    "playwright_verified": result.get("playwright_status", "") == "ok",
                    "availability_signal": sig,
                    "store_url": result.get("url", ""),
                    "_discovered": True,
                }
                if price:
                    price_key = "price_inr" if cur == "INR" else "price_usd"
                    hw[rkey][price_key] = price

    return checks


def run_enrichment(old_enrichment: dict, today: str, state: dict = None) -> dict:
    """Run deep-analysis prompts for richer modal content (analysis + links).
    
    Returns merged enrichment dict: {item_key: {analysis: str, links: [{url, title, desc}]}}.
    Falls back to cached data on failure.
    Also generates analysis for dynamically discovered hardware.
    """
    enrichment = dict(old_enrichment)

    # 1. Run static enrichment prompts
    for prompt_key, prompt_template in ENRICHMENT_PROMPTS.items():
        prompt = prompt_template.format(date=today)
        logger.info(f"--- Enrichment: {prompt_key} ---")
        response = run_copilot(prompt, timeout=240, category=f"enrich-{prompt_key}")
        if not response:
            logger.warning(f"Enrichment {prompt_key}: empty response, keeping cached")
            continue

        parsed = parse_json_response(response)
        if parsed:
            logger.info(f"Enrichment {prompt_key}: parsed ({len(parsed)} items)")
            for key, val in parsed.items():
                if isinstance(val, dict):
                    # Normalize links format
                    links = val.get("links", [])
                    if isinstance(links, list):
                        val["links"] = [
                            lnk for lnk in links
                            if isinstance(lnk, dict) and lnk.get("url", "").startswith("http")
                        ]
                    enrichment[key] = val
        else:
            logger.warning(f"Enrichment {prompt_key}: parse failed, keeping cached")

    # 2. Run dynamic enrichment for discovered hardware (if any)
    if state:
        enrichment = _run_discovery_enrichment(enrichment, today, state)

    return enrichment


def _run_discovery_enrichment(enrichment: dict, today: str, state: dict) -> dict:
    """Generate deep analysis for dynamically discovered hardware items.

    Only analyzes items that are validated/tracked and don't already have enrichment.
    Batches up to 4 items per prompt to stay within cmd line limits.
    """
    dynamic = state.get("dynamic_stores", {})
    dyn_stores = dynamic.get("stores", [])
    tracked = [s for s in dyn_stores if s.get("status") in ("validated", "tracked")]

    # Filter to items without existing enrichment (or stale >7 days)
    needs_analysis = []
    for s in tracked:
        key = s.get("key", "")
        existing = enrichment.get(key, {})
        if not existing.get("analysis"):
            needs_analysis.append(s)

    if not needs_analysis:
        logger.info("Discovery enrichment: all tracked items already have analysis")
        return enrichment

    # Batch up to 4 items per prompt (keep under cmd line limit)
    for batch_start in range(0, len(needs_analysis), 4):
        batch = needs_analysis[batch_start:batch_start + 4]
        logger.info(f"--- Discovery enrichment: {len(batch)} items ---")

        # Build dynamic prompt with items to analyze
        items_desc = []
        for s in batch:
            label = s.get("label", s.get("key", "?"))
            url = s.get("url", "")
            provenance = s.get("provenance", "")[:100]
            items_desc.append(
                f'"{s["key"]}": {{"analysis": "Analyze {label} for local LLM use. '
                f'URL: {url}. Context: {provenance}. '
                f'Cover: memory/VRAM specs, estimated tok/s for 30B-70B models, price in INR and USD, '
                f'availability in India/USA/Canada, comparison with Mac Studio M4 Max 128GB (baseline: ~160 tok/s Qwen3-30B-A3B), '
                f'fine-tuning feasibility for 7B-14B models, warranty, pros/cons for 24/7 coding agents.", '
                f'"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}'
            )

        items_json = "{" + ", ".join(items_desc) + "}"
        prompt = (
            f"You are a hardware analyst. Today is {today}. "
            f"GOAL: Evaluate hardware for 24/7 local LLM coding agents — "
            f"30B-70B inference at 25+ tok/s, 7B-14B fine-tuning. Budget INR 1.5-5L / USD 1500-5000. "
            f"Baseline: Mac Studio M4 Max 128GB = ~160 tok/s Qwen3-30B-A3B, ₹3.65L India. "
            f"Search the web and analyze each item. Return ONLY JSON: "
            + items_json +
            " 1-3 REAL URLs per item. ONLY JSON."
        )

        response = run_copilot(prompt, timeout=240, category="enrich-discovery")
        if not response:
            logger.warning("Discovery enrichment: empty response")
            continue

        parsed = parse_json_response(response)
        if parsed:
            logger.info(f"Discovery enrichment: parsed ({len(parsed)} items)")
            for key, val in parsed.items():
                if isinstance(val, dict):
                    links = val.get("links", [])
                    if isinstance(links, list):
                        val["links"] = [
                            lnk for lnk in links
                            if isinstance(lnk, dict) and lnk.get("url", "").startswith("http")
                        ]
                    enrichment[key] = val
        else:
            logger.warning("Discovery enrichment: parse failed")

    return enrichment


def build_recommendation_prompt(checks: dict, enrichment: dict, prev_recs: list,
                                today: str, state: dict = None) -> str:
    """Build a compact recommendation prompt (must stay under ~6000 chars for Windows cmd limit)."""
    # Compile concise market snapshot — key items only
    context_lines = []
    important_keys = {
        "mac_studio_m5", "mac_studio_128gb_india", "mac_studio_128gb_us",
        "mac_mini_48gb_india", "mac_mini_48gb_us",
        "framework_desktop_128gb", "bosgame_m5_128gb", "beelink_gtr9_pro_128gb",
        "minisforum_ms_s1_max", "corsair_ws300",
        "amd_strix_halo_128gb_india", "apple_refurbished",
        "rtx_5090_india", "rtx_5090_us",
        "best_local_coding_models", "fine_tuning_models",
        "coding_agent_frameworks", "inference_runtimes",
        "new_hardware_discoveries", "new_products_from_blogs",
        "strix_halo_deals", "gpu_deals_india",
    }
    for cat, data in checks.items():
        if not isinstance(data, dict):
            continue
        for key, val in data.items():
            if key not in important_keys:
                continue
            if isinstance(val, dict):
                info = val.get("info", "")[:80]
                flags = ",".join(f"{k}={v}" for k, v in val.items() if k != "info" and isinstance(v, bool))
                context_lines.append(f"{key}({flags}): {info}")

    context = "; ".join(context_lines)

    # Build compact capability sheet summary for recommendation context
    cap_summary = ""
    if state:
        sheet = state.get("capability_sheet", {})
        cap_items = []
        for key, data in sheet.items():
            name = data.get("name", key)
            mem = data.get("memory_gb", "?")
            t30 = data.get("tok_s_30b_q4")
            avail = data.get("currently_available")
            avail_str = "avail" if avail else ("OOS" if avail is False else "?")
            t30_str = f"{t30}tok/s" if t30 else "?"
            cap_items.append(f"{name}: {mem}GB, {t30_str}@30B, {avail_str}")
        if cap_items:
            cap_summary = f" Candidates: {'; '.join(cap_items[:6])}."

    # Build store availability summary from Playwright-verified data
    avail_lines = []
    if state:
        store_results = state.get("store_check", {}).get("results", [])
        for r in store_results:
            sig = r.get("availability_signal", "unknown")
            label = r.get("label", r.get("key", "?"))
            price = r.get("price")
            cur = r.get("currency", "USD")
            if sig in ("config_validated", "add_to_cart", "in_stock_schema"):
                try:
                    ps = f"₹{float(price):,.0f}" if cur == "INR" and price else (f"${float(price):,.0f}" if price else "")
                except (ValueError, TypeError):
                    ps = ""
                avail_lines.append(f"{label}: IN STOCK {ps}")
            elif sig == "out_of_stock":
                avail_lines.append(f"{label}: OUT OF STOCK")
            elif sig == "metadata_only":
                avail_lines.append(f"{label}: PAGE EXISTS BUT NOT ORDERABLE")
        avail_summary = " VERIFIED STORE STATUS: " + "; ".join(avail_lines[:10]) + "." if avail_lines else ""

    prev_text = ""
    if prev_recs:
        last = prev_recs[-1].get("data", {})
        prev_text = f" Prev: {last.get('recommendation','?')}-{last.get('best_option','?')[:40]}."

    return (
        f"You are a hardware advisor. {today}. "
        f"User: Budget INR 1.5-5L, buys from India/USA/Canada. "
        f"Goal: 24/7 YOLO coding agents. Inference 30B-70B at 25+ tok/s on 48-128GB unified memory. "
        f"Fine-tuning 7B-14B. MoE models (Qwen3-30B-A3B) are a game-changer. "
        f"Current plan: wait for Mac Studio M5 Max 128GB (WWDC Jun 2026, ship ~Oct 2026). "
        f"Ruled out: RTX 4090 (discontinued), 4070 Ti Super/5080 (16GB too small), dual GPU (unreliable). "
        f"Qwen3-30B-A3B: ~160 tok/s M4 Max, ~70 tok/s Strix Halo. "
        f"Sonnet 4.6 cloud 24/7=INR 1.32Cr/yr. Apple warranty works globally."
        f"{cap_summary}{avail_summary}{prev_text} "
        f"Market: {context} "
        "CRITICAL RULE: Do NOT recommend buy_now for any product that is NOT ORDERABLE or OUT OF STOCK. "
        "Only recommend buy_now if the product shows IN STOCK in VERIFIED STORE STATUS above. "
        "If best product is unavailable, recommend wait or consider_alternative. "
        "Search web for latest. Return ONLY JSON: "
        '{"recommendation": "buy_now or wait or consider_alternative", '
        '"best_option": "product name+config", '
        '"summary": "2-3 sentence rec", '
        '"reasoning": "2-3 paragraphs: why, price INR, availability, tok/s, alternatives, fine-tuning, wait?", '
        '"best_model": "best LLM for this HW", '
        '"model_config": "quant, ctx window, tok/s", '
        '"fine_tuning": "feasibility+approach on this HW", '
        '"cost_estimate_inr": "total INR", '
        '"buy_links": [{"url": "link", "title": "name", "desc": "brief"}], '
        '"wait_for": "what+timeline if wait", '
        '"next_milestone": "next key date/event to watch (e.g. WWDC June 9, Bosgame M5 restock, etc)", '
        '"fallback_now": "if user needs something TODAY, what is the best available option right now with price and store link", '
        '"confidence": "high or medium or low", '
        '"changed_since_last": "what changed since yesterday or first_run"} '
        "Real data only. ONLY JSON."
    )


def run_recommendation(checks: dict, enrichment: dict, prev_recs: list,
                       today: str, state: dict = None) -> dict | None:
    """Generate daily setup recommendation based on all current data."""
    prompt = build_recommendation_prompt(checks, enrichment, prev_recs, today, state=state)
    logger.info(f"--- Daily Recommendation ({len(prompt)} chars) ---")
    response = run_copilot(prompt, timeout=300, category="recommendation")
    if not response:
        logger.warning("Recommendation: empty response")
        return None
    parsed = parse_json_response(response)
    if parsed:
        logger.info(f"Recommendation: parsed ({len(parsed)} keys)")
        # Normalize buy_links
        links = parsed.get("buy_links", [])
        if isinstance(links, list):
            parsed["buy_links"] = [
                lnk for lnk in links
                if isinstance(lnk, dict) and lnk.get("url", "").startswith("http")
            ]
        return parsed
    logger.warning("Recommendation: parse failed")
    return None


def detect_changes(old_checks: dict, new_checks: dict) -> list[dict]:
    """Compare old and new check results, return list of changes."""
    changes = []
    # Boolean fields that indicate actionable status changes
    bool_fields = {"announced", "in_stock", "available", "found", "has_deals"}

    for category, new_data in new_checks.items():
        old_data = old_checks.get(category, {})
        if not isinstance(new_data, dict):
            continue

        for key, new_val in new_data.items():
            old_val = old_data.get(key)
            if old_val is None:
                continue  # First time

            if isinstance(new_val, dict) and isinstance(old_val, dict):
                for field in bool_fields:
                    if field in new_val and field in old_val:
                        if new_val[field] != old_val[field]:
                            changes.append({
                                "category": category,
                                "item": key,
                                "field": field,
                                "old": old_val[field],
                                "new": new_val[field],
                                "severity": classify_severity(key, field, new_val[field]),
                            })

    return changes


def classify_severity(item: str, field: str, new_value) -> str:
    """Classify change severity: critical, important, info."""
    critical_items = {"mac_studio_m5", "mac_studio_128gb_india"}
    important_items = {"mac_studio_128gb_us", "apple_refurbished",
                       "new_moe_models", "new_coding_models",
                       "amd_strix_halo_128gb_india"}

    # Efficiency research breakthroughs are always critical
    efficiency_keys = {"quantization_breakthroughs", "inference_engine_updates",
                       "moe_offloading", "budget_gpu_benchmarks",
                       "efficient_model_architectures", "memory_optimization",
                       "community_discoveries"}
    if item in efficiency_keys and field == "found" and new_value is True:
        return "critical"

    if item in critical_items and new_value is True:
        return "critical"
    if item in important_items and new_value is True:
        return "important"
    return "info"


# --- Efficiency research signal classification & filtering ---

_SIGNAL_LEVELS = {"noise": 0, "notable": 1, "breakthrough": 2}

_BREAKTHROUGH_KEYWORDS = [
    "2x", "3x", "10x", "100%", "half the vram",
    "previously impossible", "game-changer", "first time", "new engine",
]
_NOTABLE_KEYWORDS = [
    "new release", "update", "improvement", "optimization", "benchmark",
]
_NOISE_KEYWORDS = [
    "minor", "patch", "bug fix", "no significant", "incremental",
]


def classify_efficiency_signal(item_key: str, item_data: dict) -> str:
    """Classify an efficiency_research item as breakthrough/notable/noise.

    Trusts the LLM-provided ``signal`` field first, then applies keyword
    heuristic overrides on the ``info`` text.
    """
    llm_signal = item_data.get("signal", "notable")
    if llm_signal not in _SIGNAL_LEVELS:
        llm_signal = "notable"

    info = (item_data.get("info") or "").lower()

    for kw in _BREAKTHROUGH_KEYWORDS:
        if kw in info:
            return "breakthrough"
    for kw in _NOISE_KEYWORDS:
        if kw in info:
            return "noise"
    for kw in _NOTABLE_KEYWORDS:
        if kw in info:
            return "notable"

    return llm_signal


def filter_efficiency_results(results: dict, min_signal: str = "notable") -> dict:
    """Return only efficiency_research items at or above *min_signal*.

    Each retained item gets its ``signal`` field updated with the classified
    value from :func:`classify_efficiency_signal`.
    """
    threshold = _SIGNAL_LEVELS.get(min_signal, 1)
    filtered: dict = {}
    for key, data in results.items():
        if not isinstance(data, dict):
            continue
        classified = classify_efficiency_signal(key, data)
        if _SIGNAL_LEVELS.get(classified, 1) >= threshold:
            filtered[key] = {**data, "signal": classified}
    return filtered


def get_efficiency_breakthroughs(results: dict) -> list[dict]:
    """Return a list of items classified as breakthrough — used for notifications."""
    breakthroughs: list[dict] = []
    for key, data in results.items():
        if not isinstance(data, dict):
            continue
        if classify_efficiency_signal(key, data) == "breakthrough":
            breakthroughs.append({
                "key": key,
                "info": data.get("info", ""),
                "signal": "breakthrough",
            })
    return breakthroughs


def send_toast(title: str, message: str, severity: str = "info"):
    """Send Windows toast notification via BurntToast PowerShell module.
    
    Features:
    - Click notification → opens dashboard HTML in default browser
    - App logo image for visual identity
    - Persistent in Action Center (Scenario=Reminder + SnoozeAndDismiss)
    """
    icon = {"critical": "🚨", "important": "⚠️", "info": "ℹ️"}.get(severity, "ℹ️")
    full_title = f"{icon} LLM Hardware Monitor"

    # Escape for PowerShell
    safe_msg = message.replace("'", "''").replace('"', '`"')
    safe_title = full_title.replace("'", "''")

    # Build file:// URI so click-to-open works in any browser
    dashboard_uri = "file:///" + str(DASHBOARD_FILE).replace("\\", "/")
    dashboard_path = dashboard_uri.replace("'", "''")
    logo_path = str(MONITOR_DIR / "icon.png").replace("\\", "\\\\")

    # Use BurntToast with Reminder scenario for Action Center persistence
    ps_cmd = f"""
    Import-Module BurntToast -ErrorAction SilentlyContinue

    $logoPath = '{logo_path}'
    $dashUri = '{dashboard_path}'

    $textBinding = New-BTText -Text '{safe_title}'
    $textBinding2 = New-BTText -Text '{safe_msg}'

    $btnOpen = New-BTButton -Content 'Open Dashboard' -Arguments $dashUri -ActivationType Protocol
    $btnDismiss = New-BTButton -Dismiss
    $actions = New-BTAction -Buttons $btnOpen, $btnDismiss

    $bindingParams = @{{
        Children = $textBinding, $textBinding2
    }}

    if (Test-Path $logoPath) {{
        $img = New-BTImage -Source $logoPath -AppLogoOverride -Crop Circle
        $bindingParams['AppLogo'] = $img
    }}

    $binding = New-BTBinding @bindingParams
    $visual = New-BTVisual -BindingGeneric $binding

    # Scenario=Reminder keeps the toast in Action Center until dismissed
    $content = New-BTContent -Visual $visual -Actions $actions -Launch $dashUri -ActivationType Protocol -Scenario Reminder

    Submit-BTNotification -Content $content -UniqueIdentifier 'llm-monitor'
    """

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, timeout=15
        )
        logger.info(f"Toast sent (persistent): {title} - {message}")
    except Exception as e:
        logger.warning(f"Toast notification failed: {e}")


def write_desktop_summary(state: dict, changes: list[dict], run_status: dict):
    """Write a quick-glance summary file to the desktop, overwritten each run."""
    # Detect OneDrive Desktop redirect
    desktop_candidates = [
        Path(os.path.expandvars(r"%USERPROFILE%\OneDrive\Desktop")),
        Path(os.path.expandvars(r"%USERPROFILE%\OneDrive - Microsoft\Desktop")),
        Path(os.path.expandvars(r"%USERPROFILE%\Desktop")),
    ]
    desktop_dir = next((p for p in desktop_candidates if p.is_dir()), desktop_candidates[-1])
    summary_path = desktop_dir / "LLM-Monitor-Latest.txt"
    dashboard_uri = "file:///" + str(DASHBOARD_FILE).replace("\\", "/")

    rec = state.get("recommendation", {})
    action = rec.get("recommendation", "unknown")
    best_option = rec.get("best_option", "N/A")
    reasoning = rec.get("reasoning", "No recommendation generated yet.")
    # Truncate reasoning to 2 lines max
    reasoning_lines = reasoning.replace("\r\n", "\n").split("\n")[:2]
    reasoning_short = "\n".join(reasoning_lines)

    status_str = "OK" if all(s == "success" for s in run_status.values()) else "Partial"
    changes_summary = f"{len(changes)} change(s) detected" if changes else "No changes"

    lines = [
        "=" * 60,
        "  LLM HARDWARE MONITOR — DAILY SUMMARY",
        "=" * 60,
        "",
        f"  Run Time:       {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  Run Status:     {status_str} ({changes_summary})",
        "",
        f"  Recommendation: {action.upper().replace('_', ' ')}",
        f"  Best Option:    {best_option}",
        "",
        f"  Summary:",
        f"    {reasoning_short}",
        "",
    ]

    # Efficiency research section
    eff_data = state.get("checks", {}).get("efficiency_research", {})
    eff_highlights = []
    for eff_key, eff_val in eff_data.items():
        if not isinstance(eff_val, dict):
            continue
        sig = eff_val.get("signal", "")
        if sig == "breakthrough":
            eff_highlights.append(f"  🚨 {eff_key.replace('_', ' ').title()}: {eff_val.get('info', '')[:80]}")
        elif sig == "notable" and eff_val.get("found"):
            eff_highlights.append(f"  ⚡ {eff_key.replace('_', ' ').title()}: {eff_val.get('info', '')[:80]}")
    if eff_highlights:
        lines.append("-" * 60)
        lines.append("  EFFICIENCY RESEARCH")
        lines.extend(eff_highlights)
        lines.append("")

    lines.extend([
        "-" * 60,
        f"  Dashboard: {dashboard_uri}",
        "=" * 60,
        "",
    ])

    try:
        summary_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info(f"Desktop summary written to {summary_path}")
    except Exception as e:
        logger.warning(f"Failed to write desktop summary: {e}")


# ─── Dashboard Shared Helpers ────────────────────────────────────────────────

_DASHBOARD_CSS = """
  :root {
    --bg: #0a0e14; --surface: #131920; --card: #1a2029; --border: #262f3d;
    --text: #e6edf3; --dim: #6b7b8d; --accent: #58a6ff; --accent2: #a371f7;
    --green: #3fb950; --red: #f85149; --yellow: #d29922; --orange: #db6d28;
    --radius: 12px; --pane-bg: #0d1117;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh; line-height: 1.6;
  }

  /* ── Header ── */
  .header {
    position: sticky; top: 0; z-index: 100;
    background: rgba(10,14,20,0.92); backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border); padding: 16px 24px;
  }
  .header-top { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }
  .header h1 {
    font-size: 1.4em; font-weight: 700;
    background: linear-gradient(135deg, #58a6ff 0%, #a371f7 50%, #f778ba 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }
  .header .meta { font-size: 0.8em; color: var(--dim); }
  .header .meta b { color: var(--accent); font-weight: 500; }

  /* ── Nav Bar ── */
  .nav-bar {
    display: flex; align-items: center; gap: 4px; padding: 10px 24px;
    background: var(--surface); border-bottom: 1px solid var(--border);
    flex-wrap: wrap;
  }
  .nav-link {
    padding: 6px 16px; border-radius: 8px; font-size: 0.82em; font-weight: 500;
    color: var(--dim); text-decoration: none; transition: all 0.2s;
    border: 1px solid transparent;
  }
  .nav-link:hover { color: var(--text); background: var(--card); }
  .nav-link.active {
    color: var(--accent); background: rgba(88,166,255,0.1);
    border-color: rgba(88,166,255,0.3);
  }
  .nav-time { margin-left: auto; font-size: 0.75em; color: var(--dim); }

  /* ── Search ── */
  .search-wrap {
    padding: 12px 24px; background: var(--surface); border-bottom: 1px solid var(--border);
    position: sticky; top: 65px; z-index: 99; display: flex; flex-direction: column;
  }
  .search-row { position: relative; }
  .search-box {
    width: 100%; max-width: 600px; padding: 10px 16px 10px 40px;
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    color: var(--text); font-size: 0.95em; outline: none; transition: border-color 0.2s;
  }
  .search-box:focus { border-color: var(--accent); }
  .search-box::placeholder { color: var(--dim); }
  .search-row .icon { position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: var(--dim); pointer-events: none; }
  .filter-bar { display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }
  .filter-btn {
    padding: 5px 14px; border-radius: 20px; font-size: 0.8em;
    background: var(--card); border: 1px solid var(--border); color: var(--dim);
    cursor: pointer; transition: all 0.2s;
  }
  .filter-btn:hover, .filter-btn.active {
    background: rgba(88,166,255,0.15); color: var(--accent); border-color: var(--accent);
  }

  /* ── Status Bar ── */
  .status-bar {
    display: flex; flex-wrap: wrap; gap: 8px; padding: 16px 24px;
    background: var(--surface); border-bottom: 1px solid var(--border); overflow-x: auto;
  }
  .status-pill {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 20px; font-size: 0.78em;
    background: var(--card); border: 1px solid var(--border); white-space: nowrap;
  }
  .dot { width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }

  /* ── Content ── */
  .content { max-width: 1100px; margin: 0 auto; padding: 24px; }
  .run-status { display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }
  .run-badge { padding: 4px 12px; border-radius: 12px; font-size: 0.78em; }
  .run-badge.success { background: rgba(63,185,80,0.12); color: var(--green); }
  .run-badge.error { background: rgba(248,81,73,0.12); color: var(--red); }

  /* ── Timeline ── */
  .timeline { position: relative; }
  .timeline::before {
    content: ''; position: absolute; left: 18px; top: 0; bottom: 0;
    width: 2px; background: linear-gradient(180deg, var(--accent), var(--accent2), var(--border));
  }
  .timeline-group { margin-bottom: 32px; position: relative; }
  .timeline-date {
    display: flex; align-items: center; gap: 12px;
    padding: 8px 0; margin-left: 40px; margin-bottom: 12px;
  }
  .date-dot {
    position: absolute; left: 12px; width: 14px; height: 14px;
    border-radius: 50%; background: var(--accent); border: 3px solid var(--bg);
    box-shadow: 0 0 0 2px var(--accent);
  }
  .date-text { font-weight: 700; font-size: 1.1em; }
  .date-time { color: var(--dim); font-size: 0.8em; }
  .date-count {
    padding: 2px 10px; border-radius: 12px; font-size: 0.75em;
    background: rgba(88,166,255,0.12); color: var(--accent);
  }

  /* ── Cards ── */
  .timeline-cards {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 12px; margin-left: 40px;
  }
  .detail-cards {
    display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 12px;
  }
  .timeline-card {
    background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 16px; transition: all 0.25s ease;
    border-left: 3px solid var(--border); position: relative; overflow: hidden;
    cursor: pointer;
  }
  .timeline-card:hover {
    transform: translateY(-2px); box-shadow: 0 8px 32px rgba(0,0,0,0.35);
    border-color: var(--accent);
  }
  .timeline-card::after {
    content: '\2192'; position: absolute; top: 12px; right: 14px;
    color: var(--dim); font-size: 1.1em; transition: all 0.2s;
  }
  .timeline-card:hover::after { color: var(--accent); transform: translateX(3px); }
  .timeline-card.sev-critical {
    border-left-color: var(--red);
    background: linear-gradient(135deg, rgba(248,81,73,0.06), var(--card));
    animation: glow-red 3s ease-in-out infinite;
  }
  .timeline-card.sev-important {
    border-left-color: var(--yellow);
    background: linear-gradient(135deg, rgba(210,153,34,0.06), var(--card));
  }
  @keyframes glow-red {
    0%, 100% { box-shadow: 0 0 0 rgba(248,81,73,0); }
    50% { box-shadow: 0 0 20px rgba(248,81,73,0.15); }
  }
  .card-top { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
  .card-icon { font-size: 1.1em; }
  .card-cat { font-size: 0.75em; color: var(--dim); text-transform: uppercase; letter-spacing: 0.5px; }
  .badge {
    padding: 2px 8px; border-radius: 10px; font-size: 0.65em; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.5px;
  }
  .badge.new { background: rgba(63,185,80,0.2); color: var(--green); }
  .badge.update { background: rgba(88,166,255,0.2); color: var(--accent); }
  .card-title { font-weight: 700; font-size: 1.05em; margin-bottom: 6px; }
  .card-flags { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }
  .flag {
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 0.78em; font-weight: 500;
  }
  .card-info {
    font-size: 0.85em; color: var(--dim); line-height: 1.5;
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
  }
  .detail-hint {
    display: block; margin-top: 8px; font-size: 0.75em; color: var(--accent);
    opacity: 0.7; transition: opacity 0.2s;
  }
  .timeline-card:hover .detail-hint { opacity: 1; }

  /* ── Modal (Side Pane) ── */
  .modal-overlay {
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(0,0,0,0.55); backdrop-filter: blur(6px);
    opacity: 0; visibility: hidden; transition: all 0.3s ease;
  }
  .modal-overlay.open { opacity: 1; visibility: visible; }
  .modal-pane {
    position: absolute; right: 0; top: 0; bottom: 0;
    width: 560px; max-width: 92vw;
    background: var(--pane-bg); border-left: 1px solid var(--border);
    overflow-y: auto; overflow-x: hidden;
    transform: translateX(100%); transition: transform 0.35s cubic-bezier(0.16, 1, 0.3, 1);
    box-shadow: -12px 0 48px rgba(0,0,0,0.5);
  }
  .modal-overlay.open .modal-pane { transform: translateX(0); }
  .modal-close {
    position: sticky; top: 0; float: right; z-index: 10;
    width: 40px; height: 40px; margin: 12px 12px 0 0;
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    color: var(--dim); font-size: 1.2em; cursor: pointer;
    display: flex; align-items: center; justify-content: center; transition: all 0.2s;
  }
  .modal-close:hover { color: var(--text); background: var(--surface); }
  .modal-body { padding: 20px 28px 40px; }
  .modal-header {
    display: flex; align-items: flex-start; gap: 14px;
    margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }
  .modal-icon { font-size: 2em; line-height: 1; }
  .modal-title { font-size: 1.3em; font-weight: 700; margin-bottom: 4px; }
  .modal-cat {
    font-size: 0.8em; color: var(--accent); text-transform: uppercase;
    letter-spacing: 0.5px; font-weight: 500;
  }
  .modal-flags {
    display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 20px;
  }
  .modal-flag {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 14px; border-radius: 8px; font-size: 0.85em; font-weight: 600;
    background: var(--card); border: 1px solid var(--border);
  }
  .modal-section { margin-bottom: 24px; }
  .modal-section h3 {
    font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.8px;
    color: var(--accent); margin-bottom: 10px; font-weight: 600;
    display: flex; align-items: center; gap: 8px;
  }
  .modal-section h3::before {
    content: ''; display: inline-block; width: 3px; height: 14px;
    background: var(--accent); border-radius: 2px;
  }
  .modal-summary {
    font-size: 0.95em; line-height: 1.7; color: var(--text);
    background: var(--surface); padding: 14px 18px; border-radius: 10px;
    border: 1px solid var(--border);
  }
  .modal-analysis {
    font-size: 0.92em; line-height: 1.8; color: #c9d1d9;
    background: var(--surface); padding: 16px 20px; border-radius: 10px;
    border: 1px solid var(--border); white-space: pre-wrap;
  }
  .modal-analysis:empty { display: none; }

  /* ── Links Table ── */
  .links-table {
    width: 100%; border-collapse: collapse; font-size: 0.88em;
    background: var(--surface); border-radius: 10px; overflow: hidden;
    border: 1px solid var(--border);
  }
  .links-table th {
    text-align: left; padding: 10px 14px; font-size: 0.78em;
    text-transform: uppercase; letter-spacing: 0.5px; color: var(--dim);
    background: var(--card); border-bottom: 1px solid var(--border); font-weight: 600;
  }
  .links-table td {
    padding: 10px 14px; border-bottom: 1px solid var(--border);
    vertical-align: top;
  }
  .links-table tr:last-child td { border-bottom: none; }
  .links-table tr:hover td { background: rgba(88,166,255,0.04); }
  .links-table a {
    color: var(--accent); text-decoration: none; font-weight: 500;
    display: inline-flex; align-items: center; gap: 4px;
  }
  .links-table a:hover { text-decoration: underline; }
  .links-table a::after { content: '\2197'; font-size: 0.8em; opacity: 0.6; }
  .links-table .link-desc { color: var(--dim); font-size: 0.9em; margin-top: 2px; }
  .no-links { color: var(--dim); font-style: italic; font-size: 0.9em; padding: 12px; }

  .empty-state { text-align: center; padding: 60px 20px; color: var(--dim); font-size: 1.1em; }
  .hidden { display: none !important; }

  /* ── Recommendation Hero Card ── */
  .rec-card {
    background: linear-gradient(135deg, rgba(88,166,255,0.08) 0%, rgba(163,113,247,0.08) 50%, rgba(247,120,186,0.06) 100%);
    border: 1px solid rgba(88,166,255,0.25); border-radius: 16px;
    padding: 24px 28px; margin-bottom: 28px; position: relative; overflow: hidden; cursor: pointer;
    transition: all 0.3s ease;
  }
  .rec-card:hover {
    border-color: var(--accent); box-shadow: 0 8px 40px rgba(88,166,255,0.12);
    transform: translateY(-2px);
  }
  .rec-card::before {
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, var(--accent), var(--accent2), #f778ba);
  }
  .rec-card::after {
    content: '\2192'; position: absolute; top: 20px; right: 20px;
    color: var(--dim); font-size: 1.3em; transition: all 0.2s;
  }
  .rec-card:hover::after { color: var(--accent); transform: translateX(4px); }
  .rec-top { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
  .rec-badge {
    padding: 4px 12px; border-radius: 20px; font-size: 0.72em; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.8px;
  }
  .rec-badge.buy_now { background: rgba(63,185,80,0.2); color: var(--green); }
  .rec-badge.wait { background: rgba(210,153,34,0.2); color: var(--yellow); }
  .rec-badge.consider_alternative { background: rgba(88,166,255,0.2); color: var(--accent); }
  .rec-title { font-size: 1.2em; font-weight: 700; margin-bottom: 6px; }
  .rec-summary { font-size: 0.95em; color: #c9d1d9; line-height: 1.7; margin-bottom: 14px; }
  .rec-meta {
    display: flex; flex-wrap: wrap; gap: 16px; font-size: 0.82em; color: var(--dim);
  }
  .rec-meta-item { display: flex; align-items: center; gap: 6px; }
  .rec-meta-item b { color: var(--text); font-weight: 600; }
  .rec-extra { margin-top: 8px; padding: 6px 10px; background: rgba(255,255,255,0.03); border-radius: 6px; border-left: 3px solid var(--accent); }
  .rec-extra-item { font-size: 0.82em; color: var(--text); opacity: 0.85; }
  .rec-hint {
    display: block; margin-top: 12px; font-size: 0.75em; color: var(--accent); opacity: 0.7;
  }

  /* ── Recommendation Modal extras ── */
  .rec-section { margin-bottom: 20px; }
  .rec-section h4 {
    font-size: 0.82em; text-transform: uppercase; letter-spacing: 0.6px;
    color: var(--accent2); margin-bottom: 8px; font-weight: 600;
  }
  .rec-section-body {
    font-size: 0.92em; line-height: 1.8; color: #c9d1d9;
    background: var(--surface); padding: 14px 18px; border-radius: 10px;
    border: 1px solid var(--border); white-space: pre-wrap;
  }
  .rec-confidence {
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 12px; font-size: 0.78em; font-weight: 600;
  }
  .rec-confidence.high { background: rgba(63,185,80,0.15); color: var(--green); }
  .rec-confidence.medium { background: rgba(210,153,34,0.15); color: var(--yellow); }
  .rec-confidence.low { background: rgba(248,81,73,0.15); color: var(--red); }

  /* ── Detail Page ── */
  .page-title { font-size: 1.5em; font-weight: 700; margin-bottom: 8px; }
  .page-desc { font-size: 0.9em; color: var(--dim); margin-bottom: 24px; line-height: 1.6; }

  /* ── Footer ── */
  .footer {
    text-align: center; padding: 30px; color: var(--dim); font-size: 0.78em;
    border-top: 1px solid var(--border); margin-top: 40px;
  }
  .footer a { color: var(--accent); text-decoration: none; }

  .modal-actions { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border, #333); }
  .discuss-btn { padding: 8px 16px; border-radius: 6px; background: var(--accent, #3b82f6); color: #fff; border: none; cursor: pointer; font-size: 0.85rem; }
  .discuss-btn:hover { opacity: 0.9; }
  .copied-toast { margin-left: 12px; color: #10b981; font-size: 0.85rem; }
  #priceHistorySection { margin-top: 16px; padding: 12px; background: var(--bg, #0d1117); border-radius: 8px; }
  #priceHistorySection h3 { font-size: 1rem; margin-bottom: 8px; }
  #modalPriceMeta { font-size: 0.8rem; color: var(--dim); margin-top: 8px; }

  @media (max-width: 600px) {
    .timeline-cards, .detail-cards { grid-template-columns: 1fr; }
    .header h1 { font-size: 1.1em; }
    .content { padding: 16px; }
    .modal-pane { width: 100vw; max-width: 100vw; }
    .modal-body { padding: 16px; }
  }

  /* ── Mobile Responsive ── */
  @media (max-width: 768px) {
    body { padding: 8px; }
    .nav-bar { flex-wrap: wrap; gap: 4px; padding: 8px; }
    .nav-link { font-size: 0.75rem; padding: 4px 8px; }
    .nav-time { display: none; }
    .header h1 { font-size: 1.3rem; }
    .content { padding: 0 4px; }

    /* Readiness hero — stack vertically */
    .readiness-hero { grid-template-columns: 1fr !important; gap: 16px !important; padding: 16px !important; }
    .gauge-number { font-size: 2.5rem !important; }

    /* Decision banner */
    .decision-banner { flex-direction: column; gap: 8px; padding: 12px 16px; }
    .decision-action { font-size: 1rem; }

    /* Price ticker — wrap instead of scroll */
    .price-ticker { flex-wrap: wrap !important; gap: 8px !important; }
    .ticker-item { padding: 6px 12px !important; }

    /* Cards grid — single column */
    .timeline-grid, .card-grid, .price-grid, .learn-grid {
      grid-template-columns: 1fr !important;
    }

    /* Category links — wrap */
    .cat-links { gap: 8px !important; }
    .cat-link { font-size: 0.75rem !important; padding: 6px 10px !important; }

    /* Filter buttons — smaller */
    .filter-bar, .signal-filters { flex-wrap: wrap; gap: 4px; }
    .filter-btn, .signal-btn { font-size: 0.7rem; padding: 3px 8px; }

    /* Modal — full screen on mobile */
    .modal-pane { width: 95vw !important; max-height: 90vh !important; margin: 5vh auto !important; padding: 16px !important; }

    /* Tables — horizontal scroll */
    .model-table-wrap { overflow-x: auto; -webkit-overflow-scrolling: touch; }
    table { font-size: 0.75rem; }

    /* Learning cards */
    .learn-card { padding: 12px !important; }
    .learn-actions { flex-wrap: wrap; }

    /* Footer */
    .footer { font-size: 0.7rem; flex-direction: column; text-align: center; }
  }

  @media (max-width: 480px) {
    .nav-bar { justify-content: center; }
    .readiness-bars .bar-label { width: 80px !important; font-size: 0.75rem !important; }
    .gauge-number { font-size: 2rem !important; }
    .ticker-item { flex-wrap: wrap; justify-content: center; }
    .decision-banner { text-align: center; }
  }
"""

_MODAL_OVERLAY_HTML = """
<!-- Side Pane Modal -->
<div class="modal-overlay" id="modalOverlay" onclick="closeModal()">
  <div class="modal-pane" onclick="event.stopPropagation()">
    <button class="modal-close" onclick="closeModal()">&#10005;</button>
    <div class="modal-body">
      <div class="modal-header">
        <span class="modal-icon" id="modalIcon"></span>
        <div>
          <div class="modal-title" id="modalTitle"></div>
          <div class="modal-cat" id="modalCat"></div>
        </div>
      </div>
      <div class="modal-flags" id="modalFlags"></div>

      <div class="modal-section">
        <h3>Summary</h3>
        <div class="modal-summary" id="modalSummary"></div>
      </div>

      <div class="modal-section" id="analysisSection">
        <h3>Detailed Analysis</h3>
        <div class="modal-analysis" id="modalAnalysis"></div>
      </div>

      <div class="modal-section" id="linksSection">
        <h3>Reference Links</h3>
        <table class="links-table" id="modalLinksTable">
          <thead><tr><th>Source</th><th>Description</th></tr></thead>
          <tbody id="modalLinksBody"></tbody>
        </table>
        <div class="no-links" id="noLinks" style="display:none">No reference links available for this item.</div>
      </div>

      <div id="priceHistorySection" style="display:none">
        <h3>📈 Price History</h3>
        <div id="modalPriceChart"></div>
        <div id="modalPriceMeta"></div>
      </div>

      <div class="modal-actions">
        <button class="discuss-btn" onclick="discussInCli()">💬 Discuss in Copilot CLI</button>
        <span id="copiedToast" class="copied-toast" style="display:none">✓ Copied to clipboard!</span>
      </div>
    </div>
  </div>
</div>
"""

# __MODAL_JSON__ is replaced at runtime with actual JSON
_DASHBOARD_JS = r"""
// ── Modal Data ──
const modalData = __MODAL_JSON__;

// ── Search & Filter ──
const searchBox = document.getElementById('search');
const cards = document.querySelectorAll('.timeline-card');
const groups = document.querySelectorAll('.timeline-group');
let activeFilter = 'all';

if (searchBox) {
  searchBox.addEventListener('input', () => filterCards(searchBox.value.toLowerCase(), activeFilter));
}

document.querySelectorAll('.filter-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeFilter = btn.dataset.filter;
    filterCards(searchBox ? searchBox.value.toLowerCase() : '', activeFilter);
  });
});

function filterCards(query, filter) {
  cards.forEach(card => {
    const text = (card.dataset.search || '').toLowerCase();
    const matchesSearch = !query || text.includes(query);
    let matchesFilter = filter === 'all';
    if (filter === 'hardware') matchesFilter = text.includes('hardware');
    if (filter === 'models_and_agents') matchesFilter = text.includes('models');
    if (filter === 'deals_and_blogs') matchesFilter = text.includes('deals') || text.includes('news');
    if (filter === 'critical') matchesFilter = card.classList.contains('sev-critical') || card.classList.contains('sev-important');
    card.classList.toggle('hidden', !(matchesSearch && matchesFilter));
  });
  groups.forEach(g => {
    const vis = g.querySelectorAll('.timeline-card:not(.hidden)').length;
    g.classList.toggle('hidden', vis === 0);
  });
}

// ── Modal ──
const overlay = document.getElementById('modalOverlay');

function openModal(itemKey) {
  const item = modalData[itemKey];
  if (!item) return;

  window._currentModalSession = item.sessionName || '';
  document.getElementById('modalIcon').textContent = item.icon;
  document.getElementById('modalTitle').textContent = item.label;
  document.getElementById('modalCat').textContent = item.categoryLabel;

  // Flags
  const flagsEl = document.getElementById('modalFlags');
  flagsEl.innerHTML = '';
  if (item.flags) {
    for (const [k, v] of Object.entries(item.flags)) {
      if (typeof v === 'boolean' || typeof v === 'string') {
        const color = v === true ? 'var(--green)' : v === false ? 'var(--red)' : 'var(--dim)';
        const label = k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
        const val = typeof v === 'boolean' ? (v ? 'Yes ✓' : 'No ✗') : v;
        flagsEl.innerHTML += `<span class="modal-flag" style="border-color:${color}"><span class="dot" style="background:${color}"></span>${label}: ${val}</span>`;
      }
    }
  }

  // Summary
  document.getElementById('modalSummary').textContent = item.info || 'No summary available.';

  // Analysis
  const analysisEl = document.getElementById('modalAnalysis');
  const analysisSec = document.getElementById('analysisSection');
  analysisSec.querySelector('h3').textContent = 'Detailed Analysis';
  if (item.analysis) {
    analysisEl.innerHTML = '';
    analysisEl.textContent = item.analysis;
    analysisSec.style.display = '';
  } else {
    analysisSec.style.display = 'none';
  }

  // Links table
  const tbody = document.getElementById('modalLinksBody');
  const table = document.getElementById('modalLinksTable');
  const noLinks = document.getElementById('noLinks');
  tbody.innerHTML = '';

  if (item.links && item.links.length > 0) {
    table.style.display = '';
    noLinks.style.display = 'none';
    item.links.forEach(lnk => {
      const url = lnk.url || '';
      const title = lnk.title || url.replace(/https?:\/\//, '').split('/')[0];
      const desc = lnk.desc || lnk.description || '';
      const row = document.createElement('tr');
      row.innerHTML = `<td><a href="${url}" target="_blank" rel="noopener">${escH(title)}</a></td><td><span class="link-desc">${escH(desc)}</span></td>`;
      tbody.appendChild(row);
    });
  } else {
    table.style.display = 'none';
    noLinks.style.display = '';
  }

  // Price history
  const priceSection = document.getElementById('priceHistorySection');
  const priceData = modalData[itemKey]?.priceHistory;
  if (priceData && priceData.sparklineSvg) {
    document.getElementById('modalPriceChart').innerHTML = priceData.sparklineSvg;
    document.getElementById('modalPriceMeta').innerHTML =
      'Current: <b>' + priceData.current + '</b> | ' +
      '30d range: ' + priceData.min + ' – ' + priceData.max + ' | ' +
      'Trend: ' + priceData.trend;
    priceSection.style.display = '';
  } else {
    priceSection.style.display = 'none';
  }

  overlay.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeModal() {
  overlay.classList.remove('open');
  document.body.style.overflow = '';
}

function openRecModal() {
  const rec = modalData['__recommendation__'];
  if (!rec) return;

  window._currentModalSession = rec.sessionName || '';
  document.getElementById('modalIcon').textContent = '🎯';
  document.getElementById('modalTitle').textContent = rec.label;
  document.getElementById('modalCat').textContent = 'Daily Recommendation';

  // Flags as styled badges
  const flagsEl = document.getElementById('modalFlags');
  flagsEl.innerHTML = '';
  const action = rec.flags?.recommendation || '';
  const conf = rec.flags?.confidence || 'medium';
  flagsEl.innerHTML = `<span class="rec-badge ${action}">${action.replace(/_/g,' ')}</span>` +
    `<span class="rec-confidence ${conf}">${conf} confidence</span>`;

  // Summary
  document.getElementById('modalSummary').textContent = rec.info || '';

  // Build rich analysis with extra sections
  const analysisEl = document.getElementById('modalAnalysis');
  const analysisSec = document.getElementById('analysisSection');
  let analysisHtml = '';

  if (rec.analysis) {
    analysisHtml += `<div class="rec-section"><h4>💡 Reasoning</h4><div class="rec-section-body">${escH(rec.analysis)}</div></div>`;
  }
  if (rec.best_model) {
    analysisHtml += `<div class="rec-section"><h4>🧠 Best Model</h4><div class="rec-section-body"><b>${escH(rec.best_model)}</b>` +
      (rec.model_config ? `\n${escH(rec.model_config)}` : '') + `</div></div>`;
  }
  if (rec.fine_tuning) {
    analysisHtml += `<div class="rec-section"><h4>🔧 Fine-Tuning</h4><div class="rec-section-body">${escH(rec.fine_tuning)}</div></div>`;
  }
  if (rec.wait_for) {
    analysisHtml += `<div class="rec-section"><h4>⏳ What to Wait For</h4><div class="rec-section-body">${escH(rec.wait_for)}</div></div>`;
  }
  if (rec.cost_estimate_inr) {
    analysisHtml += `<div class="rec-section"><h4>💰 Cost Estimate</h4><div class="rec-section-body">${escH(rec.cost_estimate_inr)}</div></div>`;
  }
  if (rec.changed_since_last) {
    analysisHtml += `<div class="rec-section"><h4>🔄 What Changed</h4><div class="rec-section-body">${escH(rec.changed_since_last)}</div></div>`;
  }
  if (rec.next_milestone) {
    analysisHtml += `<div class="rec-section"><h4>📅 Next Milestone</h4><div class="rec-section-body">${escH(rec.next_milestone)}</div></div>`;
  }
  if (rec.fallback_now) {
    analysisHtml += `<div class="rec-section"><h4>⚡ Need Something Now?</h4><div class="rec-section-body">${escH(rec.fallback_now)}</div></div>`;
  }

  if (analysisHtml) {
    analysisEl.innerHTML = analysisHtml;
    analysisSec.style.display = '';
    analysisSec.querySelector('h3').textContent = 'Full Analysis';
  } else {
    analysisSec.style.display = 'none';
  }

  // Links table (buy links)
  const tbody = document.getElementById('modalLinksBody');
  const table = document.getElementById('modalLinksTable');
  const noLinks = document.getElementById('noLinks');
  tbody.innerHTML = '';

  if (rec.links && rec.links.length > 0) {
    table.style.display = '';
    noLinks.style.display = 'none';
    rec.links.forEach(lnk => {
      const url = lnk.url || '';
      const title = lnk.title || url.replace(/https?:\/\//, '').split('/')[0];
      const desc = lnk.desc || lnk.description || '';
      const row = document.createElement('tr');
      row.innerHTML = `<td><a href="${url}" target="_blank" rel="noopener">${escH(title)}</a></td><td><span class="link-desc">${escH(desc)}</span></td>`;
      tbody.appendChild(row);
    });
  } else {
    table.style.display = 'none';
    noLinks.style.display = '';
  }

  // Hide price history in recommendation modal
  const phSec = document.getElementById('priceHistorySection');
  if (phSec) phSec.style.display = 'none';
  const phChart = document.getElementById('modalPriceChart');
  if (phChart) phChart.innerHTML = '';
  const phMeta = document.getElementById('modalPriceMeta');
  if (phMeta) phMeta.innerHTML = '';

  overlay.classList.add('open');
  document.body.style.overflow = 'hidden';
}

function discussInCli() {
  const title = document.getElementById('modalTitle').textContent;
  const summary = document.getElementById('modalSummary').textContent;
  const context = summary.substring(0, 200).replace(/"/g, '\\\\"');
  const sessionName = window._currentModalSession || '';
  let cmd;
  if (sessionName) {
    cmd = 'copilot --resume="' + sessionName + '" -p "I want to discuss: ' + title + '. Context from monitor: ' + context + '"';
  } else {
    cmd = 'copilot -p "Tell me more about: ' + title + '. Context: ' + context + '"';
  }
  navigator.clipboard.writeText(cmd).then(() => {
    const toast = document.getElementById('copiedToast');
    toast.style.display = 'inline';
    setTimeout(() => { toast.style.display = 'none'; }, 2000);
  });
}

function escH(s) {
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}

// ── Keyboard shortcuts ──
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    if (overlay.classList.contains('open')) {
      closeModal();
    } else if (searchBox) {
      searchBox.value = '';
      searchBox.blur();
      filterCards('', activeFilter);
    }
  }
  if (e.key === '/' && document.activeElement !== searchBox && !overlay.classList.contains('open')) {
    e.preventDefault();
    if (searchBox) searchBox.focus();
  }
});
"""


def _esc(val):
    """HTML-escape a value for dashboard rendering."""
    import html as html_lib
    if val is None:
        return ""
    return html_lib.escape(str(val))


def _generate_nav_html(active_page: str, now: str) -> str:
    """Generate navigation bar HTML. active_page determines link targets and highlighting."""
    pages = [
        ("summary", "\U0001F4CA Summary", "index.html"),
        ("hardware", "\U0001F5A5\uFE0F Hardware", "hardware.html"),
        ("models", "\U0001F9E0 Models & Agents", "models.html"),
        ("efficiency", "\U0001F52C Efficiency", "efficiency.html"),
        ("deals", "\U0001F4B0 Deals & News", "deals.html"),
        ("learning", "\U0001F4DA Learning", "learning.html"),
        ("weekly", "\U0001F4CA Weekly", "weekly.html"),
    ]
    links = []
    for key, label, filename in pages:
        active_cls = " active" if key == active_page else ""
        if active_page == "summary":
            href = "index.html" if key == "summary" else f"pages/{filename}"
        else:
            href = "../index.html" if key == "summary" else filename
        links.append(f'<a href="{href}" class="nav-link{active_cls}">{label}</a>')
    return (
        '<nav class="nav-bar">'
        + "".join(links)
        + f'<span class="nav-time">Last: {_esc(now)}</span>'
        + "</nav>"
    )


def _build_modal_data(checks, enrichment, cat_icons, cat_labels, link_map):
    """Build modal data dict from checks and enrichment data."""
    modal_data = {}
    for cat_key, cat_data in checks.items():
        if not isinstance(cat_data, dict):
            continue
        for item_key, item_val in cat_data.items():
            entry = {
                "label": item_key.replace("_", " ").title(),
                "category": cat_key,
                "categoryLabel": cat_labels.get(cat_key, cat_key),
                "icon": cat_icons.get(cat_key, "\U0001F4E6"),
                "info": "",
                "flags": {},
                "analysis": "",
                "links": [],
                "sessionName": _session_name_for(cat_key),
            }
            if isinstance(item_val, dict):
                entry["info"] = item_val.get("info", "")
                entry["flags"] = {k: v for k, v in item_val.items() if k != "info"}
            else:
                entry["info"] = str(item_val)

            enr = enrichment.get(item_key, {})
            if isinstance(enr, dict):
                entry["analysis"] = enr.get("analysis", "")
                enr_links = enr.get("links", [])
                if isinstance(enr_links, list):
                    entry["links"] = enr_links

            if not entry["links"]:
                default_link = link_map.get(item_key, "")
                if default_link:
                    entry["links"] = [{"url": default_link, "title": entry["label"], "desc": "Default tracking link"}]

            modal_data[item_key] = entry
    return modal_data


def _generate_item_cards_html(cat_key, items_dict, cat_icons, cat_labels, enrichment):
    """Generate card HTML for all items in a category. Used by detail pages."""
    icon = cat_icons.get(cat_key, "\U0001F4E6")
    cat_label = cat_labels.get(cat_key, cat_key.replace("_", " ").title())
    cards = ""
    for item_key, item_val in items_dict.items():
        label = item_key.replace("_", " ").title()
        info = ""
        flags_html = ""
        if isinstance(item_val, dict):
            info = item_val.get("info", "")
            for bk in ("announced", "in_stock", "available", "found", "has_deals"):
                if bk in item_val:
                    val = item_val[bk]
                    color = "var(--green)" if val else "var(--dim)"
                    bl = bk.replace("_", " ").title()
                    flags_html += f'<span class="flag" style="color:{color}"><span class="dot" style="background:{color}"></span>{bl}: {"Yes" if val else "No"}</span>'
            freshness = item_val.get("freshness", "")
            if freshness == "fresh_verified":
                flags_html += '<span class="flag" style="color:var(--green)"><span class="dot" style="background:var(--green)"></span>\U0001F7E2 Fresh</span>'
            elif freshness == "partial":
                flags_html += '<span class="flag" style="color:var(--yellow, #f0c040)"><span class="dot" style="background:var(--yellow, #f0c040)"></span>\U0001F7E1 Partial</span>'
            elif freshness == "stale":
                stale_h = item_val.get("stale_hours", "?")
                flags_html += f'<span class="flag" style="color:var(--red)"><span class="dot" style="background:var(--red)"></span>\U0001F534 Stale ({stale_h}h)</span>'
        elif isinstance(item_val, str):
            info = item_val

        has_analysis = bool(enrichment.get(item_key, {}).get("analysis", ""))
        detail_indicator = '<span class="detail-hint">\U0001F4D6 Click for details</span>' if has_analysis else '<span class="detail-hint">\u2197 Click for info</span>'

        cards += (
            f'\n        <div class="timeline-card clickable" onclick="openModal(\'{_esc(item_key)}\')"'
            f'\n             data-search="{_esc(label)} {_esc(info)} {_esc(cat_label)}" data-item="{_esc(item_key)}">'
            f'\n          <div class="card-top">'
            f'\n            <span class="card-icon">{icon}</span>'
            f'\n            <span class="card-cat">{_esc(cat_label)}</span>'
            f'\n          </div>'
            f'\n          <div class="card-title">{_esc(label)}</div>'
            + (f'\n          <div class="card-flags">{flags_html}</div>' if flags_html else '')
            + f'\n          <div class="card-info">{_esc(str(info)[:150])}{"…" if len(str(info)) > 150 else ""}</div>'
            f'\n          {detail_indicator}'
            f'\n        </div>'
        )
    return cards


def _generate_page_shell(title, nav_html, body_content, modal_json):
    """Wrap content in a full HTML document with CSS, modal system, and JS."""
    monitor_dir_uri = str(MONITOR_DIR).replace("\\", "/")
    js = _DASHBOARD_JS.replace("__MODAL_JSON__", modal_json)
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="UTF-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
        f'<title>{title}</title>\n'
        f'<style>{_DASHBOARD_CSS}</style>\n'
        f'</head>\n<body data-run-id="{_RUN_ID}" data-run-date="{_RUN_DATE}">\n'
        + nav_html + '\n'
        + body_content + '\n'
        + _MODAL_OVERLAY_HTML + '\n'
        + f'<div class="footer">\n'
        f'  Powered by <a href="https://github.com/features/copilot">GitHub Copilot CLI</a> &middot;\n'
        f'  Checks daily at 9 AM via Windows Task Scheduler &middot;\n'
        f'  <a href="file:///{monitor_dir_uri}/monitor.log">View Log</a> &middot;\n'
        f'  <a href="file:///{monitor_dir_uri}/monitor_state.json">View State</a>\n'
        f'</div>\n'
        f'<script>{js}\n</script>\n'
        '</body>\n</html>'
    )


def _generate_main_page(state, checks, enrichment, cat_icons, cat_labels, link_map, modal_data, now, run_status, timeline):
    """Generate the main summary page content."""
    nav_html = _generate_nav_html("summary", now)

    # ── Inline CSS for new dashboard sections ────────────────────────────
    dashboard_css = """<style>
/* Readiness Hero */
.readiness-hero { display: grid; grid-template-columns: 200px 1fr 1fr; gap: 24px; padding: 24px; background: var(--bg2); border-radius: 12px; margin-bottom: 24px; }
.gauge-number { font-size: 3rem; font-weight: 700; color: var(--accent); }
.gauge-label { font-size: 0.9rem; color: var(--dim); }
.gauge-trend { font-size: 0.85rem; margin-top: 4px; }
.bar-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }
.bar-label { width: 120px; font-size: 0.85rem; }
.bar-track { flex: 1; height: 8px; background: var(--bg); border-radius: 4px; overflow: hidden; }
.bar-fill { height: 100%; border-radius: 4px; transition: width 0.3s; }
.bar-score { width: 30px; text-align: right; font-size: 0.85rem; font-weight: 600; }

/* Decision Banner */
.decision-banner { padding: 16px 24px; border-radius: 8px; display: flex; align-items: center; gap: 16px; margin-bottom: 24px; }
.decision-wait { background: linear-gradient(135deg, #1e3a5f, #1a1a2e); border-left: 4px solid #3b82f6; }
.decision-buy { background: linear-gradient(135deg, #1a3d2e, #1a1a2e); border-left: 4px solid #10b981; }
.decision-urgent { background: linear-gradient(135deg, #3d1a1a, #1a1a2e); border-left: 4px solid #ef4444; }
.decision-action { font-size: 1.2rem; font-weight: 700; }
.decision-detail { flex: 1; }
.decision-confidence { font-size: 0.8rem; color: var(--dim); }

/* Price Ticker */
.price-ticker { display: flex; gap: 16px; overflow-x: auto; padding: 12px 0; margin-bottom: 24px; }
.ticker-item { display: flex; align-items: center; gap: 8px; padding: 8px 16px; background: var(--bg2); border-radius: 8px; white-space: nowrap; min-width: fit-content; }
.ticker-name { font-size: 0.8rem; color: var(--dim); }
.ticker-price { font-weight: 600; }
.ticker-trend { font-size: 0.9rem; }
.trend-up { color: #ef4444; }
.trend-down { color: #10b981; }
.trend-stable { color: var(--dim); }

/* Category Links */
.cat-links { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 24px; }
.cat-link { padding: 8px 16px; background: var(--bg2); border-radius: 8px; text-decoration: none; color: var(--fg); font-size: 0.85rem; border: 1px solid var(--border, #333); }
.cat-link:hover { border-color: var(--accent); }

/* Mobile responsive */
@media (max-width: 768px) {
  .readiness-hero { grid-template-columns: 1fr; }
  .price-ticker { flex-wrap: wrap; }
}
</style>"""

    # ── 1. Readiness Hero ────────────────────────────────────────────────
    readiness = state.get("readiness_score", {})
    if readiness and isinstance(readiness, dict):
        overall = readiness.get("overall", 0)
        trend_raw = readiness.get("trend", "stable")
        trend_map = {"improving": "\u2191 improving", "declining": "\u2193 declining", "stable": "\u2192 stable"}
        trend_display = trend_map.get(trend_raw, "\u2192 stable")
        trend_color = "#10b981" if trend_raw == "improving" else "#ef4444" if trend_raw == "declining" else "var(--dim)"

        dimensions = [
            ("\U0001F5A5\uFE0F", "Hardware", readiness.get("hardware", {})),
            ("\U0001F9E0", "Models", readiness.get("models", {})),
            ("\U0001F527", "Tools", readiness.get("tools", {})),
            ("\U0001F4B0", "Cost", readiness.get("cost", {})),
        ]

        def _bar_color(score):
            if score >= 75:
                return "#10b981"
            if score >= 50:
                return "#f59e0b"
            if score >= 25:
                return "#f97316"
            return "#ef4444"

        bars_html = ""
        for icon, label, dim_data in dimensions:
            score = dim_data.get("score", 0) if isinstance(dim_data, dict) else 0
            bars_html += (
                f'<div class="bar-row">'
                f'<span class="bar-label">{icon} {_esc(label)}</span>'
                f'<div class="bar-track"><div class="bar-fill" style="width:{score}%;background:{_bar_color(score)}"></div></div>'
                f'<span class="bar-score">{score}</span>'
                f'</div>'
            )

        all_blockers = []
        for _icon, _label, dim_data in dimensions:
            if isinstance(dim_data, dict):
                for b in dim_data.get("blockers", []):
                    all_blockers.append(b)

        blockers_html = ""
        if all_blockers:
            blocker_items = "".join(f"<li>{_esc(b)}</li>" for b in all_blockers[:6])
            blockers_html = f'<div class="readiness-blockers"><h4>\U0001F6A7 Blockers</h4><ul>{blocker_items}</ul></div>'
        else:
            blockers_html = '<div class="readiness-blockers"><h4>\u2705 No blockers</h4></div>'

        hero_html = (
            f'<div class="readiness-hero">'
            f'<div class="readiness-gauge">'
            f'<div class="gauge-number">{overall}%</div>'
            f'<div class="gauge-label">Agent Readiness</div>'
            f'<div class="gauge-trend" style="color:{trend_color}">{trend_display}</div>'
            f'</div>'
            f'<div class="readiness-bars">{bars_html}</div>'
            f'{blockers_html}'
            f'</div>'
        )
    else:
        hero_html = (
            '<div class="readiness-hero">'
            '<div class="readiness-gauge">'
            '<div class="gauge-number" style="color:var(--dim)">—</div>'
            '<div class="gauge-label">Agent Readiness</div>'
            '<div class="gauge-trend" style="color:var(--dim)">Not yet scored</div>'
            '</div>'
            '<div class="readiness-bars"><div style="color:var(--dim);padding:12px">Awaiting first analysis run…</div></div>'
            '<div class="readiness-blockers"></div>'
            '</div>'
        )

    # ── 2. Decision Banner ───────────────────────────────────────────────
    rec = state.get("recommendation", {})
    if rec and isinstance(rec, dict):
        rec_action = rec.get("recommendation", "wait")
        rec_best = rec.get("best_option", "")
        rec_summary_text = rec.get("summary", "")
        rec_confidence = rec.get("confidence", "medium")
        rec_wait = rec.get("wait_for", "")

        action_map = {
            "wait": ("\u23F3 WAIT", "decision-wait"),
            "buy_now": ("\u2705 BUY NOW", "decision-buy"),
            "urgent": ("\U0001F6A8 URGENT", "decision-urgent"),
        }
        action_label, action_class = action_map.get(rec_action, ("\u23F3 WAIT", "decision-wait"))

        detail_text = rec_summary_text or rec_best or ""
        if rec_wait and not detail_text:
            detail_text = rec_wait
        if not detail_text:
            detail_text = "Analysis in progress"

        decision_html = (
            f'<div class="decision-banner {action_class}" onclick="openRecModal()" style="cursor:pointer">'
            f'<div class="decision-action">{action_label}</div>'
            f'<div class="decision-detail">{_esc(str(detail_text)[:200])}</div>'
            f'<div class="decision-confidence">{_esc(rec_confidence.title())} Confidence</div>'
            f'</div>'
        )
    else:
        decision_html = (
            '<div class="decision-banner decision-wait">'
            '<div class="decision-action">\u23F3 WAIT</div>'
            '<div class="decision-detail">No recommendation yet — awaiting first analysis</div>'
            '<div class="decision-confidence"></div>'
            '</div>'
        )

    # ── 3. Price Ticker ──────────────────────────────────────────────────
    price_history = state.get("price_history", {})
    ticker_items_html = ""
    for product_key in sorted(price_history.keys()):
        trend_data = get_price_trend(state, product_key)
        current = trend_data.get("current")
        if current is None:
            continue
        trend = trend_data.get("trend", "new")
        currency = trend_data.get("currency", "INR")
        history_slice = trend_data.get("history", [])

        trend_arrow_map = {"up": ("\u2191", "trend-up"), "down": ("\u2193", "trend-down"), "stable": ("\u2192", "trend-stable"), "new": ("\u2726", "trend-stable")}
        arrow, arrow_class = trend_arrow_map.get(trend, ("\u2192", "trend-stable"))

        if currency == "USD":
            price_str = f"${current:,.0f}"
        else:
            price_str = f"\u20B9{current:,.0f}"

        display_name = product_key.replace("_", " ").replace(" inr", "").replace(" usd", " (USD)").title()
        sparkline = generate_sparkline_svg(history_slice, width=80, height=24)

        ticker_items_html += (
            f'<div class="ticker-item">'
            f'<span class="ticker-name">{_esc(display_name)}</span>'
            f'<span class="ticker-price">{price_str}</span>'
            f'<span class="ticker-trend {arrow_class}">{arrow}</span>'
            f'<span class="ticker-spark">{sparkline}</span>'
            f'</div>'
        )

    if ticker_items_html:
        ticker_html = f'<div class="price-ticker">{ticker_items_html}</div>'
    else:
        ticker_html = ""

    # ── 4. Today's Highlights (top 5 by severity) ────────────────────────
    severity_order = {"critical": 0, "important": 1, "info": 2}
    all_highlight_items = []
    for entry in reversed(timeline):
        for item in entry.get("items", []):
            all_highlight_items.append((entry, item))

    all_highlight_items.sort(key=lambda x: severity_order.get(x[1].get("severity", "info"), 2))
    top_highlights = all_highlight_items[:5]

    timeline_html = ""
    for entry, item in top_highlights:
        ts = entry.get("timestamp", "")
        key = item.get("key", "")
        label = item.get("label", key)
        data = item.get("data", {})
        severity = item.get("severity", "info")
        is_new = item.get("is_new", False)
        cat = item.get("category", "other")
        icon = cat_icons.get(cat, "\U0001F4E6")
        cat_label_text = cat_labels.get(cat, cat.replace("_", " ").title())

        info = ""
        flags_html = ""
        if isinstance(data, dict):
            info = data.get("info", "")
            for bk in ("announced", "in_stock", "available", "found", "has_deals"):
                if bk in data:
                    val = data[bk]
                    color = "var(--green)" if val else "var(--dim)"
                    bl = bk.replace("_", " ").title()
                    flags_html += f'<span class="flag" style="color:{color}"><span class="dot" style="background:{color}"></span>{bl}: {"Yes" if val else "No"}</span>'
            freshness = data.get("freshness", "")
            if freshness == "fresh_verified":
                flags_html += '<span class="flag" style="color:var(--green)"><span class="dot" style="background:var(--green)"></span>\U0001F7E2 Fresh</span>'
            elif freshness == "partial":
                flags_html += '<span class="flag" style="color:var(--yellow, #f0c040)"><span class="dot" style="background:var(--yellow, #f0c040)"></span>\U0001F7E1 Partial</span>'
            elif freshness == "stale":
                stale_h = data.get("stale_hours", "?")
                flags_html += f'<span class="flag" style="color:var(--red)"><span class="dot" style="background:var(--red)"></span>\U0001F534 Stale ({stale_h}h)</span>'
        elif isinstance(data, str):
            info = data

        sev_class = f"sev-{severity}"
        new_badge = '<span class="badge new">NEW</span>' if is_new else '<span class="badge update">UPDATED</span>'

        has_analysis = bool(enrichment.get(key, {}).get("analysis", ""))
        detail_indicator = '<span class="detail-hint">\U0001F4D6 Click for details</span>' if has_analysis else '<span class="detail-hint">\u2197 Click for info</span>'

        timeline_html += (
            f'\n            <div class="timeline-card {sev_class} clickable" onclick="openModal(\'{_esc(key)}\')"'
            f'\n                 data-search="{_esc(label)} {_esc(info)} {_esc(cat_label_text)}" data-item="{_esc(key)}">'
            f'\n              <div class="card-top">'
            f'\n                <span class="card-icon">{icon}</span>'
            f'\n                <span class="card-cat">{_esc(cat_label_text)}</span>'
            f'\n                {new_badge}'
            f'\n              </div>'
            f'\n              <div class="card-title">{_esc(label)}</div>'
            + (f'\n              <div class="card-flags">{flags_html}</div>' if flags_html else '')
            + f'\n              <div class="card-info">{_esc(str(info)[:150])}{"…" if len(str(info)) > 150 else ""}</div>'
            f'\n              {detail_indicator}'
            f'\n            </div>'
        )

    if not timeline_html:
        timeline_html = '<div class="empty-state">No updates yet. First check will populate this timeline.</div>'
    else:
        timeline_html = (
            '<div class="timeline-group">'
            '<div class="timeline-date">'
            '<span class="date-dot"></span>'
            '<span class="date-text">\U0001F4CC Today\'s Highlights</span>'
            f'<span class="date-count">{len(top_highlights)} top update{"s" if len(top_highlights) != 1 else ""}</span>'
            '</div>'
            f'<div class="timeline-cards">{timeline_html}</div>'
            '</div>'
        )

    # ── 5. Category Quick Links ──────────────────────────────────────────
    hw_count = len(checks.get("hardware", {})) if isinstance(checks.get("hardware"), dict) else 0
    model_count = len(checks.get("models_and_agents", {})) if isinstance(checks.get("models_and_agents"), dict) else 0
    eff_count = len(checks.get("efficiency_and_research", {})) if isinstance(checks.get("efficiency_and_research"), dict) else 0
    deals_count = len(checks.get("deals_and_blogs", {})) if isinstance(checks.get("deals_and_blogs"), dict) else 0
    learning_count = len(checks.get("learning_resources", {})) if isinstance(checks.get("learning_resources"), dict) else 0

    def _link_count_label(count):
        return f"{count} item{'s' if count != 1 else ''}" if count > 0 else "New!"

    cat_links_html = (
        '<div class="cat-links">'
        f'<a class="cat-link" href="hardware.html">\U0001F5A5\uFE0F Hardware: {_link_count_label(hw_count)}</a>'
        f'<a class="cat-link" href="models.html">\U0001F9E0 Models: {_link_count_label(model_count)}</a>'
        f'<a class="cat-link" href="efficiency.html">\U0001F52C Efficiency: {_link_count_label(eff_count)}</a>'
        f'<a class="cat-link" href="deals.html">\U0001F4B0 Deals: {_link_count_label(deals_count)}</a>'
        f'<a class="cat-link" href="learning.html">\U0001F4DA Learning: {_link_count_label(learning_count)}</a>'
        '</div>'
    )

    # ── Run status badges ────────────────────────────────────────────────
    run_bar = ""
    for cat, st in run_status.items():
        ico = "\u2705" if st == "success" else "\u274C"
        run_bar += f'<span class="run-badge {st}">{ico} {cat.replace("_"," ")}</span> '

    # ── Assemble page ────────────────────────────────────────────────────
    body_content = (
        f'\n{dashboard_css}'
        '\n<div class="header">'
        '\n  <div class="header-top">'
        '\n    <h1>\U0001F5A5\uFE0F LLM Hardware Monitor</h1>'
        f'\n    <div class="meta">Last check: <b>{now}</b> &middot; {len(timeline)} runs tracked</div>'
        '\n  </div>'
        '\n</div>'
        '\n'
        '\n<div class="content">'
        f'\n  <div class="run-status">{run_bar}</div>'
        f'\n  {hero_html}'
        f'\n  {decision_html}'
        f'\n  {ticker_html}'
        f'\n  {cat_links_html}'
        f'\n  <div class="timeline" id="timeline">{timeline_html}</div>'
        '\n</div>'
    )

    modal_json = json.dumps(modal_data, ensure_ascii=False, default=str)
    return _generate_page_shell("LLM Hardware Monitor", nav_html, body_content, modal_json)


def _generate_hardware_page(checks, enrichment, cat_icons, cat_labels, modal_data, now):
    """Generate the hardware detail page."""
    nav_html = _generate_nav_html("hardware", now)
    hw_items = checks.get("hardware", {})
    if not isinstance(hw_items, dict):
        hw_items = {}

    cards_html = _generate_item_cards_html("hardware", hw_items, cat_icons, cat_labels, enrichment)
    if not cards_html:
        cards_html = '<div class="empty-state">No hardware items tracked yet.</div>'
    else:
        cards_html = f'<div class="detail-cards">{cards_html}</div>'

    body_content = (
        '\n<div class="header">'
        '\n  <div class="header-top">'
        '\n    <h1>\U0001F5A5\uFE0F LLM Hardware Monitor</h1>'
        f'\n    <div class="meta">Last check: <b>{now}</b></div>'
        '\n  </div>'
        '\n</div>'
        '\n'
        '\n<div class="content">'
        '\n  <div class="page-title">\U0001F5A5\uFE0F Hardware</div>'
        '\n  <div class="page-desc">Tracking Mac Studio M5 availability, AMD Strix Halo alternatives, Corsair AI Workstations, and other hardware options for local LLM inference.</div>'
        f'\n  {cards_html}'
        '\n</div>'
    )

    hw_modal = {k: v for k, v in modal_data.items() if v.get("category") == "hardware"}
    modal_json = json.dumps(hw_modal, ensure_ascii=False, default=str)
    return _generate_page_shell("Hardware - LLM Hardware Monitor", nav_html, body_content, modal_json)


def _generate_models_page(checks, enrichment, cat_icons, cat_labels, modal_data, now):
    """Generate the models & agents detail page."""
    nav_html = _generate_nav_html("models", now)
    model_items = checks.get("models_and_agents", {})
    if not isinstance(model_items, dict):
        model_items = {}

    cards_html = _generate_item_cards_html("models_and_agents", model_items, cat_icons, cat_labels, enrichment)
    if not cards_html:
        cards_html = '<div class="empty-state">No model or agent items tracked yet.</div>'
    else:
        cards_html = f'<div class="detail-cards">{cards_html}</div>'

    body_content = (
        '\n<div class="header">'
        '\n  <div class="header-top">'
        '\n    <h1>\U0001F5A5\uFE0F LLM Hardware Monitor</h1>'
        f'\n    <div class="meta">Last check: <b>{now}</b></div>'
        '\n  </div>'
        '\n</div>'
        '\n'
        '\n<div class="content">'
        '\n  <div class="page-title">\U0001F9E0 Models & Agents</div>'
        '\n  <div class="page-desc">Tracking new MoE models, coding models, MLX/llama.cpp framework updates, and coding agent framework developments.</div>'
        f'\n  {cards_html}'
        '\n</div>'
    )

    model_modal = {k: v for k, v in modal_data.items() if v.get("category") == "models_and_agents"}
    modal_json = json.dumps(model_modal, ensure_ascii=False, default=str)
    return _generate_page_shell("Models & Agents - LLM Hardware Monitor", nav_html, body_content, modal_json)


def _generate_efficiency_page(checks, enrichment, modal_data, now):
    """Generate the efficiency research detail page with signal filtering."""
    nav_html = _generate_nav_html("efficiency", now)
    eff_items = checks.get("efficiency_research", {})
    if not isinstance(eff_items, dict):
        eff_items = {}
    # Enrichment is stored flat by item key (not nested under "efficiency_deep")
    eff_enrichment = enrichment

    _SIGNAL_META = {
        "breakthrough": {"badge": "\U0001F6A8 Breakthrough", "css": "signal-breakthrough"},
        "notable":      {"badge": "\u2B50 Notable",      "css": "signal-notable"},
        "noise":        {"badge": "\U0001F4CB Noise",        "css": "signal-noise"},
    }

    # Build efficiency-specific modal data
    eff_modal = {}
    cards_html = ""
    counts = {"breakthrough": 0, "notable": 0, "noise": 0}

    for item_key, item_val in eff_items.items():
        if not isinstance(item_val, dict):
            continue
        signal = item_val.get("signal", "noise")
        found = item_val.get("found", False)
        info = item_val.get("info", "")
        label = item_key.replace("_", " ").title()
        counts[signal] = counts.get(signal, 0) + 1

        meta = _SIGNAL_META.get(signal, _SIGNAL_META["noise"])
        found_color = "var(--green)" if found else "var(--dim)"
        found_label = "Yes" if found else "No"

        has_deep = bool(eff_enrichment.get(item_key, {}).get("analysis", ""))
        detail_indicator = (
            '<span class="detail-hint">\U0001F4D6 Click for details</span>'
            if has_deep
            else '<span class="detail-hint">\u2197 Click for info</span>'
        )

        cards_html += (
            f'\n        <div class="timeline-card clickable" data-signal="{_esc(signal)}"'
            f'\n             onclick="openModal(\'{_esc(item_key)}\')"'
            f'\n             data-search="{_esc(label)} {_esc(info)} {_esc(signal)}" data-item="{_esc(item_key)}">'
            f'\n          <div class="card-top">'
            f'\n            <span class="signal-badge {meta["css"]}">{meta["badge"]}</span>'
            f'\n          </div>'
            f'\n          <div class="card-title">{_esc(label)}</div>'
            f'\n          <div class="card-flags">'
            f'\n            <span class="flag" style="color:{found_color}"><span class="dot" style="background:{found_color}"></span>Found: {found_label}</span>'
            f'\n          </div>'
            f'\n          <div class="card-info">{_esc(str(info)[:200])}{"…" if len(str(info)) > 200 else ""}</div>'
            f'\n          {detail_indicator}'
            f'\n        </div>'
        )

        # Build modal entry for this item
        enr = eff_enrichment.get(item_key, {})
        eff_modal[item_key] = {
            "label": label,
            "category": "efficiency_research",
            "categoryLabel": "Efficiency Research",
            "icon": "\U0001F52C",
            "info": info,
            "flags": {"found": found, "signal": signal},
            "analysis": enr.get("analysis", "") if isinstance(enr, dict) else "",
            "links": enr.get("links", []) if isinstance(enr, dict) else [],
            "sessionName": _session_name_for("efficiency_research"),
        }

    if not cards_html:
        cards_inner = '<div class="empty-state">No efficiency research data yet. This page will show quantization breakthroughs, inference engine updates, MoE offloading, and other optimization findings.</div>'
    else:
        cards_inner = f'<div class="detail-cards">{cards_html}</div>'

    # Signal filter buttons
    total = sum(counts.values())
    filter_bar = (
        '\n  <div class="filter-bar">'
        f'\n    <button class="signal-filter-btn active" data-signal="all">All ({total})</button>'
        f'\n    <button class="signal-filter-btn" data-signal="breakthrough">\U0001F6A8 Breakthroughs Only ({counts["breakthrough"]})</button>'
        f'\n    <button class="signal-filter-btn" data-signal="notable">\u2B50 Notable+ ({counts["breakthrough"] + counts["notable"]})</button>'
        '\n  </div>'
    )

    # Inline CSS for signal badges and filter buttons
    signal_css = (
        '\n<style>'
        '\n  .signal-badge { padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }'
        '\n  .signal-breakthrough { background: #dc2626; color: #fff; }'
        '\n  .signal-notable { background: #f59e0b; color: #000; }'
        '\n  .signal-noise { background: #4b5563; color: #9ca3af; }'
        '\n  .signal-filter-btn {'
        '\n    padding: 5px 14px; border-radius: 20px; font-size: 0.8em;'
        '\n    background: var(--card); border: 1px solid var(--border); color: var(--dim);'
        '\n    cursor: pointer; transition: all 0.2s;'
        '\n  }'
        '\n  .signal-filter-btn:hover, .signal-filter-btn.active {'
        '\n    background: rgba(88,166,255,0.15); color: var(--accent); border-color: var(--accent);'
        '\n  }'
        '\n</style>'
    )

    # Inline JS for signal filtering
    signal_js = (
        "\n<script>"
        "\ndocument.querySelectorAll('.signal-filter-btn').forEach(btn => {"
        "\n  btn.addEventListener('click', () => {"
        "\n    document.querySelectorAll('.signal-filter-btn').forEach(b => b.classList.remove('active'));"
        "\n    btn.classList.add('active');"
        "\n    const sig = btn.dataset.signal;"
        "\n    document.querySelectorAll('.timeline-card').forEach(card => {"
        "\n      const cs = card.dataset.signal || '';"
        "\n      if (sig === 'all') { card.style.display = ''; }"
        "\n      else if (sig === 'notable') { card.style.display = (cs === 'breakthrough' || cs === 'notable') ? '' : 'none'; }"
        "\n      else { card.style.display = cs === sig ? '' : 'none'; }"
        "\n    });"
        "\n  });"
        "\n});"
        "\n</script>"
    )

    body_content = (
        signal_css
        + '\n<div class="header">'
        '\n  <div class="header-top">'
        '\n    <h1>\U0001F5A5\uFE0F LLM Hardware Monitor</h1>'
        f'\n    <div class="meta">Last check: <b>{now}</b></div>'
        '\n  </div>'
        '\n</div>'
        '\n'
        '\n<div class="content">'
        '\n  <div class="page-title">\U0001F52C Efficiency Research</div>'
        '\n  <div class="page-desc">Tracking quantization breakthroughs, inference engine updates, MoE offloading techniques, budget GPU benchmarks, efficient architectures, memory optimization, and community discoveries.</div>'
        + filter_bar
        + f'\n  {cards_inner}'
        '\n</div>'
        + signal_js
    )

    modal_json = json.dumps(eff_modal, ensure_ascii=False, default=str)
    return _generate_page_shell("Efficiency Research - LLM Hardware Monitor", nav_html, body_content, modal_json)


def _generate_deals_page(checks, enrichment, cat_icons, cat_labels, modal_data, now):
    """Generate the deals & news detail page."""
    nav_html = _generate_nav_html("deals", now)
    deal_items = checks.get("deals_and_blogs", {})
    if not isinstance(deal_items, dict):
        deal_items = {}

    cards_html = _generate_item_cards_html("deals_and_blogs", deal_items, cat_icons, cat_labels, enrichment)
    if not cards_html:
        cards_html = '<div class="empty-state">No deals or news tracked yet.</div>'
    else:
        cards_html = f'<div class="detail-cards">{cards_html}</div>'

    body_content = (
        '\n<div class="header">'
        '\n  <div class="header-top">'
        '\n    <h1>\U0001F5A5\uFE0F LLM Hardware Monitor</h1>'
        f'\n    <div class="meta">Last check: <b>{now}</b></div>'
        '\n  </div>'
        '\n</div>'
        '\n'
        '\n<div class="content">'
        '\n  <div class="page-title">\U0001F4B0 Deals & News</div>'
        '\n  <div class="page-desc">Tracking Apple India deals, marketplace pricing, r/LocalLLaMA news, trending models, and other relevant blog posts and announcements.</div>'
        f'\n  {cards_html}'
        '\n</div>'
    )

    deals_modal = {k: v for k, v in modal_data.items() if v.get("category") == "deals_and_blogs"}
    modal_json = json.dumps(deals_modal, ensure_ascii=False, default=str)
    return _generate_page_shell("Deals & News - LLM Hardware Monitor", nav_html, body_content, modal_json)


def _extract_learning_articles(checks):
    """Extract articles from learning_feed, handling multiple response formats."""
    lf = checks.get("learning_feed", {})
    # Format 1: {"articles": [{...}, ...]}
    articles = lf.get("articles", [])
    if isinstance(articles, list) and articles:
        return articles
    # Format 2: flat dict of articles {key: {title, url, ...}}
    result = []
    for k, v in lf.items():
        if isinstance(v, dict) and "title" in v:
            result.append(v)
    if result:
        return result
    # Format 3: single article {title, url, ...} at top level
    if "title" in lf and "url" in lf:
        return [lf]
    return []


def _generate_learning_page(checks, enrichment, now):
    """Generate the learning feed page with filterable article cards."""
    nav_html = _generate_nav_html("learning", now)

    articles = _extract_learning_articles(checks)

    _SOURCE_CSS = {
        "reddit": "source-reddit",
        "youtube": "source-youtube",
        "github": "source-github",
        "blog": "source-blog",
        "hn": "source-hackernews",
        "hackernews": "source-hackernews",
    }
    _TYPE_ICONS = {
        "article": "\U0001F4C4",
        "video": "\U0001F3AC",
        "repo": "\U0001F4E6",
        "discussion": "\U0001F4AC",
        "paper": "\U0001F4D1",
    }

    cards_html = ""
    for art in articles:
        if not isinstance(art, dict):
            continue
        title = _esc(art.get("title", "Untitled"))
        url = _esc(art.get("url", "#"))
        source = art.get("source", "blog").lower()
        category = art.get("category", "models").lower()
        summary = _esc(art.get("summary", ""))
        relevance = art.get("relevance", "medium").lower()
        art_type = art.get("type", "article").lower()

        source_cls = _SOURCE_CSS.get(source, "source-blog")
        source_label = _esc(source.replace("hn", "HN").replace("hackernews", "HN").title())
        type_icon = _TYPE_ICONS.get(art_type, "\U0001F4C4")
        rel_cls = f"relevance-{relevance}" if relevance in ("high", "medium") else "relevance-medium"
        rel_label = relevance.title() + " Relevance"

        topic_esc = _esc(art.get("title", "this topic")).replace("'", "\\'")

        cards_html += (
            f'\n      <div class="learn-card" data-category="{_esc(category)}" data-source="{_esc(source)}">'
            f'\n        <div class="learn-card-top">'
            f'\n          <span class="source-badge {source_cls}">{source_label}</span>'
            f'\n          <span class="type-badge">{type_icon} {_esc(art_type.title())}</span>'
            f'\n          <span class="relevance-badge {rel_cls}">{_esc(rel_label)}</span>'
            f'\n        </div>'
            f'\n        <h3 class="learn-title"><a href="{url}" target="_blank">{title}</a></h3>'
            f'\n        <p class="learn-summary">{summary}</p>'
            f'\n        <div class="learn-actions">'
            f'\n          <a href="{url}" target="_blank" class="learn-btn">\U0001F517 Read</a>'
            f"\n          <button class=\"learn-btn\" onclick=\"copyCliCmd('{topic_esc}')\">\U0001F4AC Discuss in CLI</button>"
            f'\n        </div>'
            f'\n      </div>'
        )

    if not cards_html:
        cards_inner = (
            '<div class="empty-state">'
            'No learning content yet. The learning feed will be populated on the next monitor run.'
            '</div>'
        )
    else:
        cards_inner = f'<div class="learn-grid">{cards_html}\n    </div>'

    learning_css = """
<style>
.learn-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 16px; }
.learn-card { padding: 16px; background: var(--bg2); border-radius: 10px; border: 1px solid var(--border, #333); }
.learn-card-top { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
.learn-title { font-size: 1rem; margin-bottom: 8px; }
.learn-title a { color: var(--accent); text-decoration: none; }
.learn-title a:hover { text-decoration: underline; }
.learn-summary { font-size: 0.85rem; color: var(--dim); line-height: 1.4; margin-bottom: 12px; }
.learn-actions { display: flex; gap: 8px; }
.learn-btn { padding: 4px 12px; border-radius: 6px; font-size: 0.8rem; cursor: pointer; background: var(--bg); color: var(--fg); border: 1px solid var(--border, #333); text-decoration: none; }
.learn-btn:hover { border-color: var(--accent); }
.source-badge { padding: 2px 6px; border-radius: 3px; font-size: 0.7rem; font-weight: 600; }
.source-reddit { background: #ff4500; color: white; }
.source-youtube { background: #ff0000; color: white; }
.source-github { background: #333; color: white; }
.source-blog { background: #6366f1; color: white; }
.source-hackernews { background: #ff6600; color: white; }
.type-badge { font-size: 0.75rem; color: var(--dim); }
.relevance-badge { font-size: 0.7rem; padding: 2px 6px; border-radius: 3px; }
.relevance-high { background: #10b981; color: white; }
.relevance-medium { background: #f59e0b; color: black; }
.filter-bar { display: flex; gap: 8px; flex-wrap: wrap; margin-bottom: 16px; }
.filter-btn { padding: 4px 12px; border-radius: 6px; font-size: 0.8rem; cursor: pointer; background: var(--bg2); color: var(--fg); border: 1px solid var(--border, #333); }
.filter-btn.active { border-color: var(--accent); background: var(--accent); color: #000; }
.copy-toast { position: fixed; bottom: 24px; left: 50%; transform: translateX(-50%); background: var(--accent); color: #000; padding: 8px 20px; border-radius: 8px; font-size: 0.85rem; font-weight: 600; opacity: 0; transition: opacity 0.3s; pointer-events: none; z-index: 9999; }
.copy-toast.show { opacity: 1; }
</style>
"""

    learning_js = """
<script>
function filterLearning(type, value) {
  document.querySelectorAll('.learn-card').forEach(function(card) {
    var matches = value === 'all' || card.dataset[type] === value;
    card.style.display = matches ? '' : 'none';
  });
  document.querySelectorAll('.filter-' + type + ' .filter-btn').forEach(function(b) {
    b.classList.toggle('active', b.dataset.value === value);
  });
}
function copyCliCmd(topic) {
  var cmd = 'copilot -p "Tell me more about: ' + topic + '"';
  navigator.clipboard.writeText(cmd).then(function() {
    var toast = document.getElementById('copy-toast');
    if (toast) { toast.classList.add('show'); setTimeout(function(){ toast.classList.remove('show'); }, 1500); }
  });
}
</script>
"""

    cat_filters = [
        ("all", "All"),
        ("inference", "Inference"),
        ("training", "Training"),
        ("agents", "Agents"),
        ("hardware", "Hardware"),
        ("quantization", "Quantization"),
        ("models", "Models"),
    ]
    src_filters = [
        ("all", "All"),
        ("reddit", "Reddit"),
        ("youtube", "YouTube"),
        ("github", "GitHub"),
        ("blog", "Blog"),
        ("hn", "HN"),
    ]

    cat_btns = ""
    for val, label in cat_filters:
        active = " active" if val == "all" else ""
        cat_btns += f' <button class="filter-btn{active}" data-value="{val}" onclick="filterLearning(\'category\',\'{val}\')">{label}</button>'

    src_btns = ""
    for val, label in src_filters:
        active = " active" if val == "all" else ""
        src_btns += f' <button class="filter-btn{active}" data-value="{val}" onclick="filterLearning(\'source\',\'{val}\')">{label}</button>'

    body_content = (
        learning_css
        + '\n<div class="header">'
        '\n  <div class="header-top">'
        '\n    <h1>\U0001F5A5\uFE0F LLM Hardware Monitor</h1>'
        f'\n    <div class="meta">Last check: <b>{now}</b></div>'
        '\n  </div>'
        '\n</div>'
        '\n'
        '\n<div class="content">'
        '\n  <div class="page-title">\U0001F4DA Learning Feed</div>'
        '\n  <div class="page-desc">Curated articles, videos, and discussions for staying up-to-date on local LLM hardware and inference.</div>'
        '\n  <div class="filter-bar filter-category"><strong>Category:</strong>' + cat_btns + '</div>'
        '\n  <div class="filter-bar filter-source"><strong>Source:</strong>' + src_btns + '</div>'
        f'\n  {cards_inner}'
        '\n</div>'
        '\n<div id="copy-toast" class="copy-toast">Copied!</div>'
        + learning_js
    )

    modal_json = json.dumps({}, ensure_ascii=False)
    return _generate_page_shell("Learning Feed - LLM Hardware Monitor", nav_html, body_content, modal_json)


def _generate_weekly_page(state, checks, enrichment, now):
    """Generate the weekly report page with price trends, benchmarks, readiness, and feed previews."""
    nav_html = _generate_nav_html("weekly", now)

    # Determine week range
    today = datetime.now()
    week_start = today - timedelta(days=today.weekday())  # Monday
    week_end = week_start + timedelta(days=6)
    week_label = f"Week of {week_start.strftime('%B %d')}\u2013{week_end.strftime('%d, %Y')}"

    # Quick stats
    price_history = state.get("price_history", {})
    price_changes = 0
    for key, hist in price_history.items():
        if isinstance(hist, list) and len(hist) >= 2:
            week_cutoff = (today - timedelta(days=7)).strftime("%Y-%m-%d")
            recent = [e for e in hist if e.get("date", "") >= week_cutoff]
            if len(recent) >= 2 and recent[-1].get("price") != recent[0].get("price"):
                price_changes += 1

    eff_items = checks.get("efficiency_research", {})
    breakthroughs = 0
    notables = 0
    if isinstance(eff_items, dict):
        for v in eff_items.values():
            if isinstance(v, dict):
                sig = v.get("signal", "noise")
                if sig == "breakthrough":
                    breakthroughs += 1
                elif sig == "notable":
                    notables += 1

    new_models = 0
    model_items = checks.get("models_and_agents", {})
    if isinstance(model_items, dict):
        for v in model_items.values():
            if isinstance(v, dict) and v.get("found"):
                new_models += 1

    stats_parts = []
    if price_changes:
        stats_parts.append(f"{price_changes} price change{'s' if price_changes != 1 else ''}")
    if new_models:
        stats_parts.append(f"{new_models} new model{'s' if new_models != 1 else ''}")
    if breakthroughs:
        stats_parts.append(f"{breakthroughs} breakthrough{'s' if breakthroughs != 1 else ''}")
    if not stats_parts:
        stats_parts.append("No major changes this week")
    quick_stats = ", ".join(stats_parts)

    # ── A. Weekly Summary Header ──
    header_html = (
        '\n<div class="weekly-section">'
        f'\n  <h2>{_esc(week_label)}</h2>'
        f'\n  <p style="color:var(--dim);font-size:0.9rem;">{_esc(quick_stats)}</p>'
        '\n</div>'
    )

    # ── B. Price Trends Section ──
    price_cards_html = ""
    for key, hist in price_history.items():
        if not isinstance(hist, list) or not hist:
            continue
        trend_data = get_price_trend(state, key)
        if trend_data.get("current") is None:
            continue
        sparkline = generate_sparkline_svg(trend_data.get("history", []))
        trend = trend_data.get("trend", "new")
        change_pct = trend_data.get("change_pct", 0.0)
        currency = trend_data.get("currency", "INR")
        min_30d = trend_data.get("min_30d")
        max_30d = trend_data.get("max_30d")

        if trend == "up":
            trend_icon = "\u2191"
            trend_color = "#ef4444"
        elif trend == "down":
            trend_icon = "\u2193"
            trend_color = "#22c55e"
        else:
            trend_icon = "\u2192"
            trend_color = "#999"

        name_label = key.replace("_", " ").title()
        cur_symbol = "\u20b9" if currency == "INR" else "$"
        price_fmt = f"{cur_symbol}{trend_data['current']:,.0f}"
        range_text = ""
        if min_30d is not None and max_30d is not None:
            range_text = f"30d: {cur_symbol}{min_30d:,.0f} \u2013 {cur_symbol}{max_30d:,.0f}"

        price_cards_html += (
            f'\n      <div class="price-card">'
            f'\n        <div class="price-card-name">{_esc(name_label)}</div>'
            f'\n        <div class="price-card-price">{_esc(price_fmt)}'
            f' <span style="font-size:0.7em;color:{trend_color};">{trend_icon} {abs(change_pct):.1f}%</span></div>'
            f'\n        <div style="margin:8px 0;">{sparkline}</div>'
            f'\n        <div class="price-card-meta"><span>{_esc(range_text)}</span></div>'
            f'\n      </div>'
        )

    if price_cards_html:
        price_section = (
            '\n<div class="weekly-section">'
            '\n  <h2>\U0001F4C8 Price Trends</h2>'
            f'\n  <div class="price-grid">{price_cards_html}\n  </div>'
            '\n</div>'
        )
    else:
        price_section = (
            '\n<div class="weekly-section">'
            '\n  <h2>\U0001F4C8 Price Trends</h2>'
            '\n  <p style="color:var(--dim);">No price history recorded yet. Trends will appear after multiple runs.</p>'
            '\n</div>'
        )

    # ── C. Model Benchmark Table ──
    model_table = generate_model_comparison_html(state)
    has_models = bool(state.get("model_benchmarks", {}).get("models"))
    if not has_models:
        model_table = '<p style="color:var(--dim);">Model benchmarks will appear after the first run with the new prompts.</p>'
    model_section = (
        '\n<div class="weekly-section">'
        '\n  <h2>\U0001F9E0 Model Benchmarks</h2>'
        f'\n  <div style="overflow-x:auto;">{model_table}</div>'
        '\n</div>'
    )

    # ── D. Readiness Score Trend ──
    readiness_history = state.get("readiness_history", [])
    if readiness_history:
        sparkline_data = [{"date": e.get("date", ""), "price": e.get("overall", 0)} for e in readiness_history[-30:]]
        readiness_sparkline = generate_sparkline_svg(sparkline_data, width=200, height=40)
        latest = readiness_history[-1] if readiness_history else {}
        overall = latest.get("overall", 0)
        sub_scores = []
        for sub_key in ("hardware", "models", "tools", "cost"):
            val = latest.get(sub_key)
            if val is not None:
                sub_scores.append(f"{sub_key.title()}: {val}")
        sub_text = " | ".join(sub_scores) if sub_scores else ""
        readiness_section = (
            '\n<div class="weekly-section">'
            '\n  <h2>\U0001F3AF Readiness Score</h2>'
            f'\n  <div style="display:flex;align-items:center;gap:24px;">'
            f'\n    <div style="font-size:2rem;font-weight:700;">{overall}<span style="font-size:0.5em;color:var(--dim);">/100</span></div>'
            f'\n    <div>{readiness_sparkline}</div>'
            f'\n  </div>'
            f'\n  <div style="color:var(--dim);font-size:0.85rem;margin-top:8px;">{_esc(sub_text)}</div>'
            '\n</div>'
        )
    else:
        readiness_section = (
            '\n<div class="weekly-section">'
            '\n  <h2>\U0001F3AF Readiness Score</h2>'
            '\n  <p style="color:var(--dim);">No readiness history yet. Scores will appear after the first run.</p>'
            '\n</div>'
        )

    # ── E. Learning Feed Preview ──
    articles = _extract_learning_articles(checks)
    learning_html = ""
    source_css_map = {
        "reddit": "source-reddit",
        "youtube": "source-youtube",
        "github": "source-github",
        "blog": "source-blog",
        "hackernews": "source-hackernews",
    }
    source_icon_map = {
        "reddit": "\U0001F4AC",
        "youtube": "\U0001F3AC",
        "github": "\U0001F4BB",
        "blog": "\U0001F4DD",
        "hackernews": "\U0001F525",
    }
    for article in articles[:5]:
        if not isinstance(article, dict):
            continue
        title = article.get("title", "Untitled")
        url = article.get("url", "#")
        source = article.get("source", "blog").lower()
        category = article.get("category", "")
        summary = article.get("summary", "")
        icon = source_icon_map.get(source, "\U0001F4C4")
        badge_cls = source_css_map.get(source, "source-blog")
        learning_html += (
            f'\n    <div class="learning-item">'
            f'\n      <div class="learning-title">{icon} <a href="{_esc(url)}" target="_blank">{_esc(title)}</a>'
            f' <span class="source-badge {badge_cls}">{_esc(source)}</span>'
        )
        if category:
            learning_html += f' <span class="source-badge source-blog">{_esc(category)}</span>'
        learning_html += (
            f'</div>'
            f'\n      <div class="learning-meta">{_esc(str(summary)[:200])}{"…" if len(str(summary)) > 200 else ""}</div>'
            f'\n    </div>'
        )

    if learning_html:
        learning_section = (
            '\n<div class="weekly-section">'
            '\n  <h2>\U0001F4DA Learning Feed</h2>'
            + learning_html
            + '\n  <p style="margin-top:12px;"><a href="learning.html" style="color:var(--accent);text-decoration:none;">View all &rarr;</a></p>'
            '\n</div>'
        )
    else:
        learning_section = (
            '\n<div class="weekly-section">'
            '\n  <h2>\U0001F4DA Learning Feed</h2>'
            '\n  <p style="color:var(--dim);">No learning feed articles yet. Articles will appear after the first run.</p>'
            '\n</div>'
        )

    # ── F. Efficiency Highlights ──
    eff_data = checks.get("efficiency_research", {})
    eff_html = ""
    _SIGNAL_META = {
        "breakthrough": {"badge": "\U0001F6A8 Breakthrough", "css": "signal-breakthrough"},
        "notable":      {"badge": "\u2B50 Notable",      "css": "signal-notable"},
        "noise":        {"badge": "\U0001F4CB Noise",        "css": "signal-noise"},
    }
    if isinstance(eff_data, dict):
        for item_key, item_val in eff_data.items():
            if not isinstance(item_val, dict):
                continue
            signal = item_val.get("signal", "noise")
            if signal not in ("breakthrough", "notable"):
                continue
            info = item_val.get("info", "")
            label = item_key.replace("_", " ").title()
            meta = _SIGNAL_META.get(signal, _SIGNAL_META["noise"])
            eff_html += (
                f'\n    <div class="learning-item">'
                f'\n      <span class="signal-badge {meta["css"]}">{meta["badge"]}</span>'
                f' <span class="learning-title">{_esc(label)}</span>'
                f'\n      <div class="learning-meta">{_esc(str(info)[:200])}{"…" if len(str(info)) > 200 else ""}</div>'
                f'\n    </div>'
            )

    if eff_html:
        efficiency_section = (
            '\n<div class="weekly-section">'
            '\n  <h2>\U0001F52C Efficiency Highlights</h2>'
            + eff_html
            + '\n</div>'
        )
    else:
        efficiency_section = (
            '\n<div class="weekly-section">'
            '\n  <h2>\U0001F52C Efficiency Highlights</h2>'
            '\n  <p style="color:var(--dim);">No breakthrough or notable efficiency findings yet.</p>'
            '\n</div>'
        )

    # ── Inline CSS ──
    weekly_css = (
        '\n<style>'
        '\n.weekly-section { margin-bottom: 32px; }'
        '\n.weekly-section h2 { font-size: 1.3rem; margin-bottom: 16px; border-bottom: 1px solid var(--border, #333); padding-bottom: 8px; }'
        '\n.price-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }'
        '\n.price-card { padding: 16px; background: var(--bg2); border-radius: 8px; }'
        '\n.price-card-name { font-size: 0.85rem; color: var(--dim); }'
        '\n.price-card-price { font-size: 1.3rem; font-weight: 700; margin: 4px 0; }'
        '\n.price-card-meta { font-size: 0.8rem; color: var(--dim); display: flex; justify-content: space-between; }'
        '\n.learning-item { padding: 12px 16px; background: var(--bg2); border-radius: 8px; margin-bottom: 8px; }'
        '\n.learning-title { font-weight: 600; }'
        '\n.learning-title a { color: var(--accent); text-decoration: none; }'
        '\n.learning-meta { font-size: 0.8rem; color: var(--dim); margin-top: 4px; }'
        '\n.source-badge { padding: 2px 6px; border-radius: 3px; font-size: 0.7rem; font-weight: 600; }'
        '\n.source-reddit { background: #ff4500; color: white; }'
        '\n.source-youtube { background: #ff0000; color: white; }'
        '\n.source-github { background: #333; color: white; }'
        '\n.source-blog { background: #6366f1; color: white; }'
        '\n.source-hackernews { background: #ff6600; color: white; }'
        '\n.signal-badge { padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; }'
        '\n.signal-breakthrough { background: #dc2626; color: #fff; }'
        '\n.signal-notable { background: #f59e0b; color: #000; }'
        '\n.signal-noise { background: #4b5563; color: #9ca3af; }'
        '\n</style>'
    )

    body_content = (
        weekly_css
        + '\n<div class="header">'
        '\n  <div class="header-top">'
        '\n    <h1>\U0001F5A5\uFE0F LLM Hardware Monitor</h1>'
        f'\n    <div class="meta">Last check: <b>{now}</b></div>'
        '\n  </div>'
        '\n</div>'
        '\n'
        '\n<div class="content">'
        '\n  <div class="page-title">\U0001F4CA Weekly Report</div>'
        + header_html
        + price_section
        + model_section
        + readiness_section
        + learning_section
        + efficiency_section
        + '\n</div>'
    )

    modal_json = json.dumps({}, ensure_ascii=False, default=str)
    return _generate_page_shell("Weekly Report - LLM Hardware Monitor", nav_html, body_content, modal_json)


def generate_dashboard(state: dict, changes: list[dict], run_status: dict):
    """Generate multi-page HTML dashboard with search, cards, and detail side-pane modal."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    timeline = state.get("timeline", [])
    checks = state.get("checks", {})
    enrichment = state.get("enrichment", {})

    cat_icons = {
        "hardware": "\U0001F5A5\uFE0F",
        "models_and_agents": "\U0001F9E0",
        "efficiency_research": "\U0001F52C",
        "deals_and_blogs": "\U0001F4B0",
    }
    cat_labels = {
        "hardware": "Hardware",
        "models_and_agents": "Models & Agents",
        "efficiency_research": "Efficiency Research",
        "deals_and_blogs": "Deals & News",
    }
    link_map = {
        "mac_studio_m5": "https://www.macrumors.com/roundup/mac-studio/",
        "mac_studio_128gb_india": "https://www.apple.com/in/shop/buy-mac/mac-studio",
        "mac_studio_128gb_us": "https://www.apple.com/shop/buy-mac/mac-studio",
        "apple_refurbished": "https://www.apple.com/in/shop/refurbished/mac/mac-studio",
        "wwdc_apple_event": "https://developer.apple.com/wwdc26/",
        "corsair_ws300_india": "https://www.amazon.in/s?k=Corsair+AI+Workstation",
        "amd_strix_halo_128gb_india": "https://www.amazon.in/s?k=AMD+Strix+Halo+128GB",
        "new_moe_models": "https://huggingface.co/models?sort=trending",
        "new_coding_models": "https://huggingface.co/models?sort=trending&search=code",
        "mlx_llama_cpp": "https://github.com/ml-explore/mlx",
        "coding_agents": "https://github.com/topics/coding-agent",
        "apple_india_deals": "https://www.apple.com/in/shop/buy-mac/mac-studio",
        "mac_studio_marketplace": "https://www.amazon.in/s?k=Mac+Studio",
        "latest_local_llm_news": "https://www.reddit.com/r/LocalLLaMA/top/?t=week",
        "trending_models": "https://huggingface.co/models?sort=trending",
    }

    # Build modal data for all items
    modal_data = _build_modal_data(checks, enrichment, cat_icons, cat_labels, link_map)

    # Add recommendation to modal data
    rec = state.get("recommendation", {})
    if rec:
        modal_data["__recommendation__"] = {
            "label": rec.get("best_option", "Daily Recommendation"),
            "category": "recommendation",
            "categoryLabel": "Daily Recommendation",
            "icon": "\U0001F3AF",
            "info": rec.get("summary", ""),
            "flags": {
                "recommendation": rec.get("recommendation", ""),
                "confidence": rec.get("confidence", ""),
            },
            "analysis": rec.get("reasoning", ""),
            "links": rec.get("buy_links", []),
            "best_model": rec.get("best_model", ""),
            "model_config": rec.get("model_config", ""),
            "fine_tuning": rec.get("fine_tuning", ""),
            "wait_for": rec.get("wait_for", ""),
            "changed_since_last": rec.get("changed_since_last", ""),
            "cost_estimate_inr": rec.get("cost_estimate_inr", ""),
            "next_milestone": rec.get("next_milestone", ""),
            "fallback_now": rec.get("fallback_now", ""),
            "sessionName": _session_name_for("recommendation"),
        }

    # Enrich modal data with price history (try both key and key_usd/key_inr variants)
    for product_key in state.get("price_history", {}):
        # Match price_history keys to modal_data keys (strip _usd/_inr suffix)
        modal_key = product_key
        if product_key not in modal_data:
            if product_key.endswith("_usd"):
                modal_key = product_key[:-4]
            elif product_key.endswith("_inr"):
                modal_key = product_key[:-4]
        if modal_key in modal_data:
            trend = get_price_trend(state, product_key)
            if trend["current"] is not None:
                modal_data[modal_key]["priceHistory"] = {
                    "current": f"{trend['currency']} {trend['current']:,.0f}",
                    "min": f"{trend['min_30d']:,.0f}" if trend['min_30d'] else "—",
                    "max": f"{trend['max_30d']:,.0f}" if trend['max_30d'] else "—",
                    "trend": {"up": "↑ Rising", "down": "↓ Falling", "stable": "→ Stable", "new": "🆕 New"}[trend["trend"]],
                    "sparklineSvg": generate_sparkline_svg(trend["history"], width=200, height=40),
                }

    # Create pages directory
    os.makedirs(PAGES_DIR, exist_ok=True)

    # Generate and write main page
    main_html = _generate_main_page(state, checks, enrichment, cat_icons, cat_labels, link_map, modal_data, now, run_status, timeline)
    DASHBOARD_FILE.write_text(main_html, encoding="utf-8")
    INDEX_FILE.write_text(main_html, encoding="utf-8")

    # Generate and write detail pages
    hw_html = _generate_hardware_page(checks, enrichment, cat_icons, cat_labels, modal_data, now)
    (PAGES_DIR / "hardware.html").write_text(hw_html, encoding="utf-8")

    models_html = _generate_models_page(checks, enrichment, cat_icons, cat_labels, modal_data, now)
    (PAGES_DIR / "models.html").write_text(models_html, encoding="utf-8")

    eff_html = _generate_efficiency_page(checks, enrichment, modal_data, now)
    (PAGES_DIR / "efficiency.html").write_text(eff_html, encoding="utf-8")

    deals_html = _generate_deals_page(checks, enrichment, cat_icons, cat_labels, modal_data, now)
    (PAGES_DIR / "deals.html").write_text(deals_html, encoding="utf-8")

    learning_html = _generate_learning_page(checks, enrichment, now)
    (PAGES_DIR / "learning.html").write_text(learning_html, encoding="utf-8")

    weekly_html = _generate_weekly_page(state, checks, enrichment, now)
    (PAGES_DIR / "weekly.html").write_text(weekly_html, encoding="utf-8")

    logger.info(f"Dashboard updated: {DASHBOARD_FILE} + {PAGES_DIR}")


# ─── Main Execution ─────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("LLM Hardware Monitor - Starting daily check")
    logger.info("=" * 60)

    state = load_state()
    old_checks = state.get("checks", {})
    new_checks = {}
    run_status = {}
    today = datetime.now().strftime("%B %d, %Y")

    # Run each category prompt
    first_category = True
    for category, prompt_template in PROMPTS.items():
        if not first_category:
            time.sleep(5)  # brief pause between API calls to avoid .cmd race conditions
        first_category = False
        logger.info(f"--- Checking: {category} ---")
        prompt = prompt_template.format(date=today)
        # Inject dynamic context (known facts, discoveries, model history) per category
        dynamic_context = build_dynamic_prompt_context(state, category=category)
        if dynamic_context:
            prompt = prompt + " " + dynamic_context

        response, parsed = run_copilot_with_retry(prompt, category=category)

        if parsed:
            new_checks[category] = parsed
            run_status[category] = "success"
            logger.info(f"{category}: parsed successfully ({len(parsed)} items)")
        else:
            run_status[category] = "error"
            if response:
                logger.error(f"{category}: failed to parse JSON after retries")
                raw_file = MONITOR_DIR / f"raw_{category}_{datetime.now().strftime('%Y%m%d')}.txt"
                raw_file.write_text(response, encoding="utf-8")
            else:
                logger.error(f"Empty response for {category} after retries")
            # Keep old data for this category
            if category in old_checks:
                new_checks[category] = old_checks[category]

    # Verify store availability via Playwright (direct scraping across all stores)
    logger.info("--- Store availability checks (Playwright + HTTP fallbacks) ---")
    store_results = check_store_availability(state=state)
    if store_results:
        new_checks = merge_playwright_results(new_checks, store_results)
        state["store_check"] = {
            "timestamp": datetime.now().isoformat(),
            "results": store_results,
        }
        ok_count = sum(1 for r in store_results if r.get("playwright_status", "").startswith("ok"))
        fb_count = sum(1 for r in store_results if r.get("fallback_used"))
        stale_count = sum(1 for r in store_results if r.get("fallback_used") == "stale_cache")
        logger.info(
            f"Store checks: {len(store_results)} total, {ok_count} primary OK, "
            f"{fb_count} used fallback ({stale_count} stale cache)"
        )

        # Update dynamic store lifecycle (promote/quarantine/prune)
        state = update_dynamic_stores(state, store_results)
    else:
        logger.info("Store checks: no results (all methods failed)")

    # Record daily price snapshots for trend tracking
    state = record_price_history(state, store_results or [], new_checks)

    # Discover new hardware from monitoring results (zero API cost)
    logger.info("--- Dynamic discovery: scanning results for new hardware ---")
    candidates = extract_discoveries_from_results(new_checks, state)
    if candidates:
        dynamic = state.setdefault("dynamic_stores", {"stores": [], "pruned": []})
        existing_keys = {s["key"] for s in dynamic["stores"]}
        added = 0
        for c in candidates:
            if c["key"] not in existing_keys:
                dynamic["stores"].append(c)
                existing_keys.add(c["key"])
                added += 1
                logger.info(f"  New candidate discovered: {c['key']} from {c['discovered_from']}")
        logger.info(f"Discovery: {len(candidates)} candidates found, {added} new added")
    else:
        logger.info("Discovery: no new candidates found")

    # Detect changes
    changes = detect_changes(old_checks, new_checks) if old_checks else []

    # Update capability sheet with latest store data
    state = update_capability_sheet(state, store_results=store_results)

    # Run enrichment — change-driven: skip if no changes and enrichment is fresh
    old_enrichment = state.get("enrichment", {})
    enrichment_age_days = 999
    if state.get("enrichment_timestamp"):
        try:
            enr_dt = datetime.fromisoformat(state["enrichment_timestamp"])
            enrichment_age_days = (datetime.now() - enr_dt).days
        except (ValueError, TypeError):
            pass

    should_enrich = (
        enrichment_age_days >= 1  # at least 1 day old
        or changes  # something changed
        or not old_enrichment  # never run before
    )

    if should_enrich:
        logger.info("--- Enrichment: running (change detected or stale) ---")
        enrichment = run_enrichment(old_enrichment, today, state=state)
        state["enrichment"] = enrichment
        state["enrichment_timestamp"] = datetime.now().isoformat()
    else:
        logger.info("--- Enrichment: skipped (no changes, data fresh) ---")
        enrichment = old_enrichment

    # Update capability sheet with enrichment data too
    state = update_capability_sheet(state, enrichment=enrichment)

    # Generate daily recommendation based on all data + user constraints
    prev_recs = state.get("recommendations", [])
    rec_data = run_recommendation(new_checks, enrichment, prev_recs, today, state=state)
    if rec_data:
        state["recommendation"] = rec_data
        rec_entry = {"date": datetime.now().strftime("%Y-%m-%d"), "data": rec_data}
        if "recommendations" not in state:
            state["recommendations"] = []
        state["recommendations"].append(rec_entry)
        state["recommendations"] = state["recommendations"][-90:]
    else:
        logger.warning("Recommendation generation failed, keeping previous")

    # Compute readiness score
    readiness = compute_readiness_score(state, new_checks)
    state["readiness_score"] = readiness
    # Track history for trend
    if "readiness_history" not in state:
        state["readiness_history"] = []
    state["readiness_history"].append({
        "date": datetime.now().strftime("%Y-%m-%d"),
        "overall": readiness["overall"],
        "hardware": readiness["hardware"]["score"],
        "models": readiness["models"]["score"],
        "tools": readiness["tools"]["score"],
        "cost": readiness["cost"]["score"],
    })
    state["readiness_history"] = state["readiness_history"][-90:]
    logger.info(
        f"Readiness score: {readiness['overall']}/100 "
        f"(HW:{readiness['hardware']['score']} Model:{readiness['models']['score']} "
        f"Tools:{readiness['tools']['score']} Cost:{readiness['cost']['score']}) "
        f"Trend: {readiness['trend']}"
    )

    # Send notifications — ALWAYS notify so user sees result even at 3:30 AM
    if changes:
        critical = [c for c in changes if c["severity"] == "critical"]
        important = [c for c in changes if c["severity"] == "important"]

        if critical:
            msg = " | ".join(f"{c['item']}: {c['new']}" for c in critical)
            send_toast("🚨 CRITICAL UPDATE", msg[:200], "critical")
        elif important:
            msg = " | ".join(f"{c['item']}: {c['new']}" for c in important)
            send_toast("⚠️ Important Update", msg[:200], "important")
        else:
            send_toast("ℹ️ Minor Updates", f"{len(changes)} minor changes detected", "info")

        logger.info(f"Detected {len(changes)} changes: {len(critical)} critical, {len(important)} important")
    else:
        if old_checks:
            logger.info("No changes detected since last run")
            rec = state.get("recommendation", {})
            action = rec.get("recommendation", "wait")
            best = rec.get("best_option", "N/A")
            send_toast("✅ Daily Check Complete", f"No changes. Action: {action}. Best: {best}"[:200], "info")
        else:
            logger.info("First run — establishing baseline")
            send_toast("✅ Monitor Started", "LLM Hardware Monitor is now active!", "info")

    # Check for efficiency breakthroughs using signal classification heuristics
    efficiency_data = new_checks.get("efficiency_research", {})
    breakthroughs = get_efficiency_breakthroughs(efficiency_data)
    if breakthroughs:
        bt = breakthroughs[0]
        send_toast("🚨 EFFICIENCY BREAKTHROUGH",
                   f"{bt['key'].replace('_', ' ').title()}: {bt['info'][:150]}",
                   "critical")

    # Update checks before summary so desktop summary has current data
    state["checks"] = new_checks
    state = process_model_benchmarks(state, new_checks)
    state["last_run"] = datetime.now().isoformat()

    # Always write desktop summary file as fallback notification
    write_desktop_summary(state, changes, run_status)

    # Build a rich timeline entry with full data for each item that changed or is new
    timeline_entry = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "date_label": datetime.now().strftime("%B %d, %Y"),
        "run_status": run_status,
        "changes": changes,
        "items": [],  # individual items with info
    }

    # For each category/item, include it in timeline if:
    #   - This is the first run (all items are new)
    #   - The "info" text changed from last run
    for category, cat_data in new_checks.items():
        if not isinstance(cat_data, dict):
            continue
        old_cat = old_checks.get(category, {})
        for item_key, item_val in cat_data.items():
            old_item = old_cat.get(item_key)
            is_first = old_item is None
            info_changed = False
            if isinstance(item_val, dict) and isinstance(old_item, dict):
                info_changed = item_val.get("info") != old_item.get("info")
            elif item_val != old_item:
                info_changed = True

            if is_first or info_changed:
                # Determine severity
                severity = "info"
                if isinstance(item_val, dict):
                    for bk in ("announced", "in_stock", "available", "found", "has_deals"):
                        if item_val.get(bk) is True:
                            severity = classify_severity(item_key, bk, True)
                            break

                timeline_entry["items"].append({
                    "category": category,
                    "key": item_key,
                    "label": item_key.replace("_", " ").title(),
                    "data": item_val,
                    "severity": severity,
                    "is_new": is_first,
                })

    # Only add timeline entry if there are items to show
    if timeline_entry["items"]:
        if "timeline" not in state:
            state["timeline"] = []
        state["timeline"].append(timeline_entry)
        # Keep last 180 entries (~6 months daily)
        state["timeline"] = state["timeline"][-180:]

    # Legacy history for backwards compat
    state["history"].append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "changes_count": len(changes),
        "status": "success" if all(s == "success" for s in run_status.values()) else "partial",
        "changes": changes[:5],
    })
    state["history"] = state["history"][-90:]
    state["copilot_sessions"] = {
        "run_id": _RUN_ID,
        "run_date": _RUN_DATE,
        "pattern": f"llm-monitor-{{category}}-{_RUN_DATE}-{_RUN_ID}",
    }
    save_state(state)

    # Generate dashboard
    generate_dashboard(state, changes, run_status)

    # Auto-push to GitHub Pages (if remote is configured)
    try:
        remote_check = subprocess.run(
            ["git", "-C", str(MONITOR_DIR), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=10,
        )
        if remote_check.returncode == 0 and remote_check.stdout.strip():
            subprocess.run(
                ["git", "-C", str(MONITOR_DIR), "add", "-A"],
                capture_output=True, timeout=30,
            )
            subprocess.run(
                ["git", "-C", str(MONITOR_DIR), "commit", "-m",
                 f"Daily update {datetime.now().strftime('%Y-%m-%d %H:%M')}",
                 "--allow-empty"],
                capture_output=True, timeout=30,
            )
            push_result = subprocess.run(
                ["git", "-C", str(MONITOR_DIR), "push"],
                capture_output=True, text=True, timeout=60,
            )
            if push_result.returncode == 0:
                logger.info("Auto-pushed to GitHub Pages")
            else:
                logger.warning(f"Git push failed: {push_result.stderr[:200]}")
    except Exception as e:
        logger.warning(f"Auto-push skipped: {e}")

    logger.info("Daily check complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
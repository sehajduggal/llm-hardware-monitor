#!/usr/bin/env python3
"""
LLM Homelab — Local LLM Analytical Portal
==========================================
Auto-updating portal that researches, cross-references, and presents
grounded multi-option analysis about local LLM inference:
- Hardware (GPUs, CPUs, unified memory machines)
- Models (coding, reasoning, MoE)
- Optimization (quantization, engines, configs)
- Setup guides, knowledge graph, progressive learning

Pipeline: Gather (parallel) → Analyze → Critique → Present
Output: Static HTML on GitHub Pages (auto-push)

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
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    return f"llm-homelab-{category}-{_RUN_DATE}-{_RUN_ID}"

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

# ─── Knowledge State: Progressive Learning DAG ──────────────────────────────

TOPIC_DAG = {
    # Layer 0 - Foundations
    "tokens_and_parameters": {"layer": 0, "prereqs": [], "title": "Tokens, Parameters & Model Sizes", "goal_tags": ["build-local-coding-rig"]},
    "vram_calculation": {"layer": 0, "prereqs": ["tokens_and_parameters"], "title": "VRAM Calculation: How Much Memory Do You Need?", "goal_tags": ["build-local-coding-rig", "budget-optimized"]},
    "context_window_math": {"layer": 0, "prereqs": ["tokens_and_parameters"], "title": "Context Windows: Size, Cost & Why It Matters for Agents", "goal_tags": ["long-context-agents"]},

    # Layer 1 - Core Inference
    "prefill_vs_decode": {"layer": 1, "prereqs": ["tokens_and_parameters"], "title": "Prefill vs Decode: The Two Phases of LLM Inference", "goal_tags": ["run-70b-class-models"]},
    "kv_cache_growth": {"layer": 1, "prereqs": ["context_window_math"], "title": "KV Cache: The Hidden Memory Cost of Long Conversations", "goal_tags": ["long-context-agents"]},
    "memory_bandwidth_vs_compute": {"layer": 1, "prereqs": ["vram_calculation"], "title": "Memory Bandwidth vs Compute: What Actually Bottlenecks Inference?", "goal_tags": ["build-local-coding-rig"]},
    "latency_vs_throughput": {"layer": 1, "prereqs": ["prefill_vs_decode"], "title": "Latency vs Throughput vs Concurrency for Coding Agents", "goal_tags": ["long-context-agents"]},

    # Layer 2 - Optimization
    "quantization_basics": {"layer": 2, "prereqs": ["vram_calculation"], "title": "Quantization 101: Shrinking Models to Fit Your Hardware", "goal_tags": ["budget-optimized", "run-70b-class-models"]},
    "gguf_formats": {"layer": 2, "prereqs": ["quantization_basics"], "title": "GGUF Formats: Q4_K_M, Q5_K_M, Q8_0 — Which to Choose?", "goal_tags": ["run-70b-class-models"]},
    "exl2_awq_gptq": {"layer": 2, "prereqs": ["quantization_basics"], "title": "EXL2, AWQ, GPTQ: GPU-Optimized Quantization Formats", "goal_tags": ["run-70b-class-models"]},
    "offloading_gpu_cpu_disk": {"layer": 2, "prereqs": ["memory_bandwidth_vs_compute"], "title": "Offloading: Splitting Models Across GPU, CPU & Disk", "goal_tags": ["budget-optimized", "build-local-coding-rig"]},
    "moe_expert_routing": {"layer": 2, "prereqs": ["offloading_gpu_cpu_disk"], "title": "MoE Expert Routing: Why Mixture-of-Experts Changes Everything", "goal_tags": ["run-70b-class-models", "budget-optimized"]},
    "speculative_decoding": {"layer": 2, "prereqs": ["prefill_vs_decode"], "title": "Speculative Decoding: Using Small Models to Speed Up Big Ones", "goal_tags": ["run-70b-class-models"]},

    # Layer 3 - Engines & Runtimes
    "llama_cpp": {"layer": 3, "prereqs": ["gguf_formats", "offloading_gpu_cpu_disk"], "title": "llama.cpp: The Swiss Army Knife of Local Inference", "goal_tags": ["build-local-coding-rig"]},
    "ktransformers": {"layer": 3, "prereqs": ["moe_expert_routing"], "title": "KTransformers: GPU+CPU MoE Offloading Engine", "goal_tags": ["budget-optimized"]},
    "vllm_tensorrt": {"layer": 3, "prereqs": ["latency_vs_throughput"], "title": "vLLM & TensorRT-LLM: High-Throughput Serving Engines", "goal_tags": ["long-context-agents"]},
    "mlx_apple_silicon": {"layer": 3, "prereqs": ["memory_bandwidth_vs_compute"], "title": "MLX: Apple Silicon's Native LLM Framework", "goal_tags": ["build-local-coding-rig"]},
    "runtime_compat": {"layer": 3, "prereqs": ["llama_cpp"], "title": "CUDA vs ROCm vs Metal: Runtime Compatibility Guide", "goal_tags": ["build-local-coding-rig"]},

    # Layer 4 - Models
    "dense_vs_moe": {"layer": 4, "prereqs": ["moe_expert_routing"], "title": "Dense vs MoE Models: Architecture Trade-offs for Local Use", "goal_tags": ["run-70b-class-models"]},
    "coding_model_traits": {"layer": 4, "prereqs": ["latency_vs_throughput"], "title": "What Makes a Good Coding Model? Benchmarks That Matter", "goal_tags": ["build-local-coding-rig"]},
    "reasoning_chains": {"layer": 4, "prereqs": ["coding_model_traits"], "title": "Reasoning Models: Chain-of-Thought for Complex Code Tasks", "goal_tags": ["long-context-agents"]},
    "model_selection_for_agents": {"layer": 4, "prereqs": ["reasoning_chains", "vram_calculation"], "title": "Choosing the Right Model for 24/7 Coding Agents", "goal_tags": ["build-local-coding-rig", "long-context-agents"]},

    # Layer 5 - Agents & Application
    "agent_context_management": {"layer": 5, "prereqs": ["kv_cache_growth", "context_window_math"], "title": "Agent Context Management: Keeping Long Conversations Alive", "goal_tags": ["long-context-agents"]},
    "yolo_coding_mode": {"layer": 5, "prereqs": ["model_selection_for_agents"], "title": "YOLO Coding Mode: Autonomous Agents That Write & Execute Code", "goal_tags": ["build-local-coding-rig"]},
    "batch_concurrency": {"layer": 5, "prereqs": ["latency_vs_throughput"], "title": "Running Multiple Agents: Batch Size & Concurrency", "goal_tags": ["long-context-agents"]},
    "tool_use_function_calling": {"layer": 5, "prereqs": ["yolo_coding_mode"], "title": "Tool Use & Function Calling: How Agents Interact with Code", "goal_tags": ["build-local-coding-rig"]},

    # Layer 6 - Hardware Mapping
    "vram_tiers_and_gpus": {"layer": 6, "prereqs": ["vram_calculation", "quantization_basics"], "title": "GPU VRAM Tiers: Which Card Runs Which Model?", "goal_tags": ["build-local-coding-rig", "budget-optimized"]},
    "ram_bandwidth_for_offload": {"layer": 6, "prereqs": ["offloading_gpu_cpu_disk"], "title": "RAM Bandwidth: Why DDR5-6400 Matters for CPU Offloading", "goal_tags": ["build-local-coding-rig"]},
    "pcie_lanes_multi_gpu": {"layer": 6, "prereqs": ["vram_tiers_and_gpus"], "title": "PCIe Lanes & Multi-GPU: When One GPU Isn't Enough", "goal_tags": ["build-local-coding-rig"]},
    "ssd_weight_loading": {"layer": 6, "prereqs": ["offloading_gpu_cpu_disk"], "title": "SSD Speed & Model Loading: NVMe Matters", "goal_tags": ["build-local-coding-rig"]},
    "power_thermals_noise": {"layer": 6, "prereqs": [], "title": "Power, Thermals & Noise: 24/7 Operation Considerations", "goal_tags": ["build-local-coding-rig"]},
    "os_runtime_friction": {"layer": 6, "prereqs": ["runtime_compat"], "title": "Windows vs Linux vs macOS: Practical Runtime Differences", "goal_tags": ["build-local-coding-rig"]},

    # Layer 7 - Fine-tuning
    "lora_qlora_basics": {"layer": 7, "prereqs": ["quantization_basics"], "title": "LoRA & QLoRA: Fine-Tuning Models on Consumer Hardware", "goal_tags": ["run-70b-class-models"]},
    "when_to_finetune": {"layer": 7, "prereqs": ["model_selection_for_agents"], "title": "When to Fine-Tune vs Prompt Engineering vs RAG", "goal_tags": ["build-local-coding-rig"]},
}

USER_GOALS = [
    {"id": "build-local-coding-rig", "label": "Build a local coding rig"},
    {"id": "run-70b-class-models", "label": "Run 30B-70B class models locally"},
    {"id": "long-context-agents", "label": "Run long-context YOLO agents 24/7"},
    {"id": "budget-optimized", "label": "Best performance per rupee (₹1.5-3.5L)"},
]

# Static summaries for knowledge graph — always shown regardless of learning status
TOPIC_SUMMARIES = {
    "tokens_and_parameters": "A model's size is measured in parameters—each parameter is a learned weight stored as a floating-point number. A 70B parameter model has 70 billion weights. At FP16 (2 bytes each), that's 140 GB just for weights. Tokens are the atomic units of text: most English words split into 1-2 tokens via BPE encoding, with the average being ~1.3 tokens per word. Common tokenizers use vocabularies of 32K-128K tokens, and each token maps to an integer ID that the model processes.",
    "vram_calculation": "Total VRAM needed = model weights + KV cache + activation memory + framework overhead. Model weight memory is straightforward: parameters × bytes_per_parameter at your chosen quantization. KV cache grows with context length and batch size. A practical formula: VRAM_GB ≈ (params_B × bits_per_weight / 8) + (KV_cache_GB) + 0.5-1 GB overhead. Always add 10-15% buffer because CUDA memory fragmentation and framework allocations consume extra space.",
    "context_window_math": "Context window defines the maximum number of tokens a model can process in one pass. Memory cost scales linearly with context length due to KV cache: each additional token adds a fixed amount of memory per layer. For coding agents, context window is critical—a typical codebase prompt with file contents, instructions, and history easily reaches 8-16K tokens, making 32K+ context a practical requirement.",
    "prefill_vs_decode": "LLM inference has two distinct phases: prefill (processing the entire input prompt in parallel) and decode (generating tokens one at a time autoregressively). Prefill is compute-bound—it processes all input tokens simultaneously using matrix multiplications that saturate GPU cores. Decode is memory-bandwidth-bound—each new token requires reading the entire model weights from memory but only computes a single token's output. This is why Time-To-First-Token depends on prompt length, while tokens-per-second during generation depends on memory bandwidth.",
    "kv_cache_growth": "KV cache stores the key and value projections for every token in the context, across all attention layers. It's the 'hidden cost' because it grows linearly with sequence length and can rival or exceed model weight memory at long contexts. For a 70B model at 32K context, KV cache alone can consume 10+ GB. This is why you can load a model fine but OOM mid-conversation as context grows. Managing KV cache is the central challenge of running long-context models locally.",
    "memory_bandwidth_vs_compute": "For single-user local inference (batch size 1), memory bandwidth is almost always the bottleneck, not TFLOPS. During token generation, every parameter must be read from VRAM to compute one token's output—so your tokens/sec ≈ memory_bandwidth / model_size_in_bytes. This is why an RTX 4090 (1 TB/s) and an A100 (2 TB/s) produce tokens at roughly 2:1 ratio despite the A100 having far more TFLOPS. It's also why quantization helps speed: Q4 models are ~4× faster than FP16 because there are 4× fewer bytes to read.",
    "latency_vs_throughput": "Latency (time-to-first-token and time-per-token for a single request) trades off against throughput (total tokens/second across all concurrent requests) based on batch size and hardware utilization. For interactive coding agents, low latency matters most—a developer needs sub-2-second responses—but for autonomous 24/7 batch agents, throughput dominates since no human is waiting. Increasing batch size improves throughput near-linearly until VRAM saturates, but degrades individual request latency by 20-50% per doubling.",
    "quantization_basics": "Quantization reduces model weights from high-precision floating point (FP16, 16 bits) to lower-precision integers (INT8, INT4), shrinking VRAM usage and increasing inference speed at the cost of some quality loss. INT8 loses ~0.1-0.5% on benchmarks, and INT4 loses ~1-3% depending on method and model size. Larger models tolerate aggressive quantization better—a 70B at Q4 often outperforms a 13B at FP16. The key tradeoff: lower bits = less memory = faster generation, but with diminishing quality below 4 bits.",
    "gguf_formats": "GGUF is the quantization format used by llama.cpp and its ecosystem (Ollama, LM Studio, koboldcpp). It offers a spectrum from Q2_K (2-bit) to Q8_0 (8-bit), with K-quant variants (K_S, K_M, K_L) using importance-weighted mixed precision—keeping attention layers at higher precision while compressing less critical layers more aggressively. The IQ series (IQ4_XS, IQ3_M) uses learned quantization grids for better quality at extreme compression. Q4_K_M is the community's sweet spot for quality/size balance.",
    "exl2_awq_gptq": "EXL2, AWQ, and GPTQ are GPU-optimized quantization formats that use custom CUDA kernels for fast inference, unlike GGUF which targets CPU+GPU flexibility. GPTQ was the first popular GPU quant method; AWQ improves on it by preserving salient weights identified through activation patterns. EXL2 (ExLlamaV2) offers variable bits-per-weight with per-layer optimization, achieving the best quality-per-bit. All three require full GPU VRAM—they don't support CPU offloading gracefully.",
    "offloading_gpu_cpu_disk": "Layer offloading splits a model's transformer layers between GPU VRAM, system RAM, and optionally disk, enabling running models larger than available VRAM at reduced speed. Each layer not on GPU adds latency proportional to the PCIe/memory bandwidth bottleneck. The optimal strategy is maximizing GPU layers while keeping KV cache in VRAM; even partial GPU offloading provides significant speedup over pure CPU inference.",
    "moe_expert_routing": "Mixture-of-Experts (MoE) models like Mixtral 8x7B and DeepSeek-V2 replace dense FFN layers with multiple expert sub-networks, activating only a subset (typically 2 of 8) per token via a learned router. This means a 47B-parameter Mixtral model only uses ~13B parameters per forward pass, giving near-70B quality at 13B inference speed. MoE is exceptionally suited for offloading because inactive experts can remain in CPU RAM until routed—only active experts need GPU VRAM.",
    "speculative_decoding": "Speculative decoding uses a small, fast 'draft' model to generate candidate token sequences that are then verified in parallel by the larger target model, accepting tokens that match via rejection sampling. This exploits the fact that scoring N tokens in parallel costs roughly the same latency as generating one token. The technique guarantees mathematically identical output distribution—it trades extra compute for reduced wall-clock latency. Works best when the draft model has high acceptance rates.",
    "llama_cpp": "llama.cpp is a high-performance C/C++ inference engine for LLMs that runs on diverse hardware with zero dependencies, using the GGUF model format with aggressive quantization (1.5-bit to 8-bit). It provides CPU+GPU hybrid inference, an OpenAI-compatible HTTP server (llama-server), and supports 100+ model architectures including multimodal models. It is the de facto standard for local/edge LLM deployment on consumer hardware, with backends for Metal, CUDA, ROCm, and Vulkan.",
    "ktransformers": "KTransformers is a CPU-GPU heterogeneous inference framework specifically optimized for Mixture-of-Experts models like DeepSeek-V3/R1, enabling massive models to run on consumer hardware by intelligently placing hot experts on GPU and cold experts on CPU with AMX/AVX-optimized kernels. It achieves 3-28x speedup over naive CPU offloading through NUMA-aware memory management. The framework supports running DeepSeek-R1 (671B parameters) on a single 24GB GPU + 382GB system RAM.",
    "vllm_tensorrt": "vLLM is the leading open-source LLM serving engine featuring PagedAttention for near-zero KV cache waste, continuous batching for maximum GPU utilization, and support for 200+ model architectures. It provides an OpenAI-compatible API server with tensor/pipeline/expert parallelism for multi-GPU setups. TensorRT-LLM (NVIDIA) offers similar capabilities with deeper NVIDIA-specific optimizations; vLLM now integrates TensorRT-LLM attention kernels for best-of-both-worlds performance.",
    "mlx_apple_silicon": "MLX is Apple's official array computation framework designed specifically for Apple Silicon (M1-M4), featuring unified memory architecture that eliminates CPU-GPU data transfer overhead—arrays live in shared memory accessible by both CPU and GPU without copies. It provides NumPy-like Python APIs with PyTorch-style neural network modules. The mlx-lm ecosystem enables running quantized LLMs at competitive speeds on Mac hardware, making it the native choice for Apple Silicon inference.",
    "runtime_compat": "The GPU compute runtime landscape is dominated by NVIDIA CUDA (most mature, largest ecosystem), with AMD ROCm as the primary open competitor, Apple Metal for Apple Silicon, and Vulkan as a cross-platform fallback. CUDA lock-in is the central tension: most ML libraries target CUDA first or exclusively, making NVIDIA GPUs the path of least resistance. ROCm has reached usable maturity for major frameworks but lags in kernel availability, while Metal is Apple-only and Vulkan offers portability at a performance cost.",
    "dense_vs_moe": "Dense models activate all parameters for every token (Llama, Qwen), giving predictable VRAM usage and simpler deployment. MoE models (Mixtral, DeepSeek-V3) route each token to a subset of expert layers, achieving higher quality per compute FLOP but requiring full model weights in memory. For local inference, MoE delivers disproportionate intelligence-per-token but demands large VRAM for all expert weights. Dense models are simpler to quantize and fit cleanly into fixed VRAM budgets.",
    "coding_model_traits": "Effective coding models combine strong instruction following (IF-Eval >84%), large context windows (32K-128K tokens) for repository-scale understanding, structured output for tool use, and high scores on execution-based benchmarks like HumanEval, LiveCodeBench, and SWE-Bench. The best local coding models balance these traits with practical inference speed—Qwen2.5-Coder-32B and DeepSeek-Coder-V2 exemplify this. For autonomous agents, instruction adherence and tool-call formatting reliability matter more than raw benchmark scores.",
    "reasoning_chains": "Chain-of-thought (CoT) reasoning models like DeepSeek-R1 and QwQ-32B generate explicit step-by-step thinking tokens before producing final answers, dramatically improving performance on complex multi-step problems. DeepSeek-R1 achieves this through pure reinforcement learning, naturally emerging self-verification and reflection behaviors. For coding tasks, CoT enables decomposing problems, considering edge cases, and validating solutions—yielding 65.9% on LiveCodeBench-COT vs 37.6% without. The tradeoff is 2-10x more output tokens.",
    "model_selection_for_agents": "For 24/7 autonomous coding agents, model selection must balance sustained throughput, VRAM footprint, instruction reliability, and quality-per-token. Dense models in the 14B-32B range at Q4-Q5 quantization hit the sweet spot for single-GPU homelab deployments. The ideal agent model prioritizes instruction-following consistency and tool-use formatting over raw benchmark scores. Multi-model architectures—pairing a fast small model for routine tasks with a reasoning model for complex problems—maximize throughput while maintaining quality.",
    "agent_context_management": "Coding agents manage finite context windows (128K-200K tokens) by selectively loading relevant code via retrieval strategies like embedding-based search, AST-aware chunking, and file-level summaries. For large codebases (100K+ files), agents use a hierarchy: a compressed 'map' of repo structure stays in context while full file contents are loaded on-demand via tool calls. Effective agents spend 30-50% of context on retrieved code, 10-20% on system prompts, and reserve the rest for reasoning and conversation history.",
    "yolo_coding_mode": "YOLO mode refers to autonomous code generation where the agent executes changes, runs commands, and iterates without human approval—used in Cursor, Claude Code, and Aider. This enables 10-50x faster iteration but introduces risks: runaway modifications, accidental deletion, secret exposure, and infinite loops. Effective guardrails include sandboxed execution, git-based rollback, allowlists for commands, token budgets per task, and file-path restrictions. Most effective for well-scoped tasks with clear test suites.",
    "batch_concurrency": "Running multiple coding agents simultaneously requires careful VRAM partitioning, as each concurrent context consumes memory proportional to model size plus KV-cache. With a 7B model at Q4 (~4.5GB base), each additional concurrent request adds 1-8GB for KV-cache depending on context length—a 24GB GPU supports 2-4 concurrent agents. Continuous batching (vLLM/TGI) dynamically inserts new requests into running batches, achieving 3-8x higher throughput than naive sequential processing.",
    "tool_use_function_calling": "Tool use enables LLMs to call external functions (file read/write, shell commands, web search) by outputting structured JSON matching predefined schemas. Models are trained specifically on function-calling datasets to reliably produce valid JSON with correct parameter names; this capability varies significantly across models. Each tool call adds latency (generation + execution + re-ingestion) and token overhead (schemas in system prompt), making tool-heavy agentic workflows 3-10x more expensive than single-shot generation.",
    "vram_tiers_and_gpus": "GPU VRAM is the primary bottleneck for local LLM inference—it determines the largest model you can run entirely on-GPU at full speed. Consumer GPUs top out at 24GB (RTX 4090/5090), workstation cards like RTX A6000 offer 48GB, and multi-GPU setups reach 80-192GB. In India, price-per-GB-VRAM is worst at the consumer tier and best when buying used professional cards. For coding agents running 70B-class models, 48GB+ VRAM is the sweet spot.",
    "ram_bandwidth_for_offload": "When a model doesn't fit entirely in VRAM, layers are offloaded to system RAM, and inference speed becomes bottlenecked by RAM bandwidth. DDR4-3200 delivers ~50 GB/s while DDR5-6000 reaches ~90-96 GB/s in dual-channel, directly translating to tokens/second for offloaded layers. For a 70B model with 50% offload, the difference can mean 3-4 tok/s vs 6-8 tok/s. Quad-channel platforms (Threadripper, Xeon) can double effective bandwidth to 150-200 GB/s.",
    "pcie_lanes_multi_gpu": "Multi-GPU LLM inference requires adequate PCIe bandwidth to transfer tensor data between GPUs. Each PCIe 4.0 x16 slot provides ~32 GB/s bidirectional, but most consumer platforms only have one true x16 slot—the second often runs at x8, halving bandwidth. For inference (not training), PCIe bandwidth is less critical since most data stays on-device, but tensor-parallel inference still needs decent interconnect. NVLink (600-900 GB/s) eliminates this but is unavailable on consumer GPUs.",
    "ssd_weight_loading": "SSD speed determines how fast you can load model weights into RAM/VRAM—a 70B Q4 model is ~40GB on disk, and loading can take 6-40 seconds depending on storage. NVMe Gen4 drives (5-7 GB/s) load models 3-5× faster than SATA SSDs (~550 MB/s) and are the sweet spot for price/performance. For coding agents that keep models resident in memory, load time only matters at startup or model-switching.",
    "power_thermals_noise": "Running LLM inference hardware 24/7 in India creates significant power, thermal, and noise challenges. An RTX 4090 draws 300-350W during inference; combined with CPU/RAM/cooling, a full system consumes 500-700W continuously—costing ₹3,000-₹6,000/month. Ambient temperatures of 35-45°C in Indian summers demand robust cooling, and GPU fans at high RPM create 45-55 dBA noise. Undervolt your GPU, use a UPS for power stability, and consider a separate room.",
    "os_runtime_friction": "The choice of OS significantly impacts LLM inference setup complexity. Linux (Ubuntu 22.04/24.04) provides the smoothest path with native NVIDIA driver and CUDA support, first-class Docker GPU passthrough, and is the primary target for most inference engines. Windows requires WSL2 for most tooling and introduces GPU memory overhead of 200-500MB. macOS is viable only for Apple Silicon via Metal/MLX but lacks CUDA entirely.",
    "lora_qlora_basics": "LoRA (Low-Rank Adaptation) fine-tunes LLMs by freezing base weights and injecting small trainable rank-decomposition matrices into attention layers, reducing trainable parameters from billions to 1-50 million. QLoRA extends this by loading the frozen base in 4-bit quantization, cutting VRAM by 60-70% while maintaining training quality. This means you can fine-tune a 7B model on a single 24GB GPU or a 13B model with QLoRA, producing a small adapter file (10-200MB) that merges at inference time.",
    "when_to_finetune": "Fine-tuning is the highest-effort intervention and should only be chosen after prompt engineering and RAG have been evaluated. Prompt engineering costs zero training compute and handles 70-80% of formatting and behavior needs. RAG is ideal when the model needs private/current knowledge without baking it into weights. Fine-tuning becomes necessary when you need consistent stylistic behavior, domain-specific reasoning patterns, or must reduce inference-time token usage by eliminating lengthy system prompts.",
}

# Detailed key facts for knowledge graph side panel — specific, quantitative, actionable
TOPIC_KEY_FACTS = {
    "tokens_and_parameters": [
        "1 parameter at FP16 = 2 bytes, at FP32 = 4 bytes, at Q4 = 0.5 bytes",
        "A 7B model at FP16 requires ~14 GB, at Q4 requires ~3.5-4 GB (plus overhead)",
        "Average English text: ~4 characters per token, ~1.3 tokens per word, so 1000 words ≈ 1300 tokens",
        "Code is less token-efficient: Python averages ~2.5 tokens per word-equivalent due to symbols and indentation",
        "LLaMA-family models use 32K-token vocabulary; newer models (Qwen2, GPT-4) use 128K-152K vocabularies",
        "Parameter count follows: params ≈ 12 × n_layers × d_model² for standard transformer architectures",
    ],
    "vram_calculation": [
        "Weight memory: 70B × Q4_K_M ≈ 70 × 0.56 = ~39 GB; 70B × Q5_K_M ≈ 70 × 0.69 = ~48 GB",
        "Common sizes per param: Q3_K_M=0.44B, Q4_K_M=0.56B, Q5_K_M=0.69B, Q6_K=0.81B, Q8_0=1.0B, FP16=2.0B",
        "A 24GB GPU (RTX 4090) can run: 7B at FP16, 13B at Q5, 34B at Q4, or 70B at Q2-Q3 (not recommended)",
        "Two 24GB GPUs with tensor parallelism: 70B at Q4_K_M fits with room for 8K context",
        "If model weights fill >85% of VRAM, you'll OOM once KV cache grows at longer contexts",
        "Activation memory during inference is small (~200-500 MB) compared to training where it dominates",
    ],
    "context_window_math": [
        "KV cache per token = 2 × n_layers × n_kv_heads × head_dim × bytes_per_element",
        "LLaMA-3 70B (GQA 8 KV heads): KV per token = ~327 KB in FP16; at 32K context = ~10.5 GB",
        "Doubling context from 4K to 8K doubles KV cache memory but does NOT double model weight memory",
        "Coding agents routinely need 16K-32K context: system prompt (1-2K) + files (5-15K) + history (5-10K)",
        "Context window memory is the main reason 128K-context models need much more VRAM than param count suggests",
        "Quantizing KV cache to Q4 in llama.cpp reduces context memory by 75% with <1% quality loss",
    ],
    "prefill_vs_decode": [
        "Prefill throughput on RTX 4090: ~2000-5000 tokens/sec for 7B Q4; decode: ~80-120 tokens/sec same model",
        "Prefill is compute-bound: GPU utilization 70-95% with large matrix ops keeping all cores busy",
        "Decode is bandwidth-bound: GPU utilization drops to 5-15% because each token reads all weights",
        "TTFT for 4K-token prompt on 70B Q4 (RTX 4090): ~8-15 seconds; for 7B Q4: ~0.5-1 second",
        "For coding agents, long system prompts make prefill latency significant—cache KV states when possible",
        "Prompt caching (reusing KV from previous prefill) eliminates repeated prefill cost for static system prompts",
    ],
    "kv_cache_growth": [
        "KV cache formula: 2 × n_layers × n_kv_heads × head_dim × seq_len × bytes_per_element",
        "LLaMA-3 70B (GQA): KV per token = 327 KB in FP16; at 32K context ≈ 10.5 GB",
        "Without GQA, a 70B model at 32K would need ~80+ GB for KV cache alone",
        "KV cache quantization to Q4 reduces cache memory by 75% with <1% perplexity increase",
        "Sliding window attention (Mistral-style) caps KV cache regardless of total context length",
        "For coding agents generating long outputs, KV cache grows continuously—monitor VRAM during generation",
    ],
    "memory_bandwidth_vs_compute": [
        "RTX 4090: 1,008 GB/s → 7B Q4 (~4GB): theoretical max ~250 tok/s; actual ~100-130 tok/s",
        "Theoretical decode tok/s ≈ bandwidth_GB_s / model_size_GB × efficiency (0.4-0.6)",
        "Apple Silicon advantage: unified memory means no PCIe bottleneck; M4 Max 546 GB/s, M2 Ultra 800 GB/s",
        "Dual 4090s: ~2 TB/s effective bandwidth, making 70B Q4 viable at ~25-35 tok/s",
        "The arithmetic intensity of decode is ~1 FLOP/byte—far below GPU peak efficiency (100+ FLOPs/byte)",
        "RTX 4090 (1 TB/s) outperforms RTX 4060 (272 GB/s) by ~3.5× for inference, despite only 2× the TFLOPS",
    ],
    "latency_vs_throughput": [
        "Single-request on 7B (RTX 4090): ~50ms TTFT, ~30-50 tok/s; batch of 4: 20-35 tok/s per request, 80-140 aggregate",
        "For 24/7 agents, optimize throughput: 4090 produces 4000-8000 tokens/minute at batch=4 vs 1800-3000 at batch=1",
        "Memory bandwidth is the bottleneck for small batches; fully utilized at batch=4-8 for 7B models",
        "Speculative decoding reduces per-request latency 2-3x but doesn't improve throughput",
        "For tool-heavy agents (20-50 calls/task): wall-clock dominated by sequential round-trips, not raw speed",
        "Sweet spot for homelab: batch_size=2-3 gives 70-85% peak throughput with <5s per-agent latency",
    ],
    "quantization_basics": [
        "FP16 uses 2 bytes/param: 70B = ~140GB; INT8 halves to ~70GB; INT4 quarters to ~35GB",
        "INT8 is nearly lossless for most models, with perplexity increase under 0.5%",
        "For coding tasks, Q4 is the practical minimum—below this, code accuracy degrades noticeably",
        "Rule of thumb: use the largest model that fits at Q4-Q5 rather than a smaller model at higher precision",
        "Below 3-bit (Q2_K, IQ2), quality degrades rapidly—only viable for very large models as a last resort",
        "Quantization speed impact: INT4 can be 20-40% faster than FP16 due to reduced bandwidth pressure",
    ],
    "gguf_formats": [
        "Q4_K_M: ~4.85 bits/weight, ~4.4GB per 7B params—best general-purpose choice, minimal quality loss",
        "Q5_K_M: ~5.69 bpw, ~5.1GB per 7B—nearly indistinguishable from FP16 in blind tests",
        "Q8_0: ~8.5 bpw, virtually lossless—use when VRAM allows (e.g., 34B on 48GB GPU)",
        "IQ4_XS: ~4.25 bpw, ~15% smaller than Q4_K_M with similar quality—excellent for tight VRAM",
        "Q6_K: ~6.56 bpw—sweet spot for 24GB GPUs running 7-13B models wanting near-perfect quality",
        "File size: (params × bits_per_weight) / 8 + overhead; 70B Q4_K_M ≈ 40GB file, needs ~42GB to run",
    ],
    "exl2_awq_gptq": [
        "EXL2 supports arbitrary target bpp (3.5, 4.25, 5.0) with per-layer bit allocation—attention gets more bits",
        "AWQ is 1.5-2x faster than GPTQ at same bit width; supported in vLLM, TGI, and transformers",
        "EXL2 at 4.0 bpw matches or beats GPTQ/AWQ 4-bit quality with fine-grained tuning",
        "Compatibility: AWQ→vLLM/HuggingFace/TGI; GPTQ→AutoGPTQ; EXL2→ExLlamaV2/TabbyAPI only",
        "For multi-agent: AWQ preferred if you need vLLM's continuous batching",
        "All three require calibration data; coding-focused calibration improves code retention by 5-10%",
    ],
    "offloading_gpu_cpu_disk": [
        "PCIe 4.0 x16: ~25GB/s; each offloaded 70B layer (~0.5GB) adds ~20ms latency per token",
        "DDR5 RAM: CPU inference on 70B Q4 achieves ~5-8 tok/s vs 30-40 fully on GPU",
        "Every 10% of layers offloaded to CPU reduces speed by ~15-25% due to PCIe overhead",
        "Disk offloading (mmap) yields 0.5-2 tok/s—impractical for interactive use",
        "llama.cpp -ngl flag controls GPU layers: --ngl 99 for full GPU",
        "Prioritize fitting full model on GPU at lower quant (Q4) rather than higher quant with offloading",
    ],
    "moe_expert_routing": [
        "Mixtral 8x7B: 47B total, activates ~13B per token (2-of-8)—needs ~26GB VRAM at Q4",
        "DeepSeek-V2: 236B total, 21B active per token—GPT-4-class coding at fraction of compute",
        "MoE offloading: only 2 experts (25% of FFN) needed per token; 75% can stay in RAM",
        "Expert prefetching hides offloading latency—llama.cpp and ExLlamaV2 support speculative expert loading",
        "Shared attention (~30% of params) should always be on GPU; expert layers can be offloaded",
        "Mixtral Q4_K_M on 24GB GPU: ~20 layers on GPU + expert offload → 15-25 tok/s",
    ],
    "speculative_decoding": [
        "Typical speedup: 1.5-3x for autoregressive decoding with no quality loss",
        "Draft models are 10-20x smaller (e.g., 68M draft for 7B target, 1B for 70B target)",
        "Gains diminish on creative/unpredictable text where draft acceptance drops below 50%",
        "Speculation length typically 3-8 tokens per step; longer risks lower acceptance rates",
        "vLLM supports: n-gram, separate draft, EAGLE, Medusa, and suffix-based methods",
        "llama.cpp: --draft flag with separate GGUF draft model, achieving 1.5-2x on consumer GPUs",
    ],
    "llama_cpp": [
        "Supports 1.5-bit (IQ1_S) to 8-bit quantization; Q4_K_M most popular for coding models",
        "llama-server: OpenAI-compatible API with continuous batching, parallel slots, embeddings, WebUI",
        "CPU+GPU hybrid: run 70B Q4 (~40GB) across 24GB GPU + system RAM seamlessly",
        "Apple Silicon first-class: Metal backend + ARM NEON + Accelerate framework",
        "Supports speculative decoding, grammar-constrained output (GBNF), LoRA hot-swapping, multimodal",
        "RTX 4090 throughput: 80-120 tok/s for 7B Q4, 25-40 tok/s for 70B Q4 full GPU offload",
    ],
    "ktransformers": [
        "DeepSeek-R1/V3 on single 24GB GPU + 382GB DRAM at 14-16 tok/s output with Q4",
        "Supports AMX-INT8, AMX-BF16, AVX512 CPU backends for Intel; also AMD ROCm and Ascend NPU",
        "Integrates with SGLang for production serving and LLaMA-Factory for fine-tuning",
        "139K context length for DeepSeek-V3/R1 in 24GB VRAM using FP8 kernels",
        "Expert scheduling: hot experts on GPU, cold experts on CPU—maximizes GPU utilization",
        "Supports DeepSeek-V4-Flash, Kimi-K2, Qwen3-MoE, LLaMA-4, and other MoE architectures",
    ],
    "vllm_tensorrt": [
        "PagedAttention reduces KV cache waste from 60-80% to near 0%—enables 2-4x higher batch sizes",
        "Continuous batching: 5-23x higher throughput than static batching in production workloads",
        "Supports FP8, INT8, INT4, GPTQ, AWQ, GGUF, and compressed-tensors quantization formats",
        "Multi-GPU: tensor parallel, pipeline parallel, data parallel, expert parallel modes",
        "Disaggregated prefill/decode for latency optimization; prefix caching for repeated prompts",
        "Supports NVIDIA, AMD (ROCm), x86/ARM CPUs, Google TPUs, Intel Gaudi, Apple Silicon",
    ],
    "mlx_apple_silicon": [
        "Unified memory = zero-copy CPU-GPU sharing; 64GB M4 Max loads 60GB model without PCIe bottleneck",
        "mlx-lm: M4 Max achieves ~40-50 tok/s on 70B Q4, ~100+ tok/s on 7B-8B models",
        "M4 Max: 546 GB/s unified bandwidth; M3 Ultra: 800 GB/s—critical for memory-bound inference",
        "Available via pip install mlx; also has C++, C, and Swift APIs for native app integration",
        "MLX now supports CUDA backend for Linux—becoming cross-platform beyond macOS",
        "Lacks multi-node distributed inference; best for single-machine local inference and research",
    ],
    "runtime_compat": [
        "CUDA: 15+ years maturity, cuBLAS/cuDNN/TensorRT provide optimized kernels unavailable elsewhere",
        "ROCm: supports PyTorch, vLLM, llama.cpp; AMD RX 7900 XTX (24GB, ~$900) best non-NVIDIA option",
        "Metal: macOS/iOS only, used by llama.cpp, MLX, Ollama—excellent for Apple Silicon",
        "Vulkan: cross-platform but 20-40% slower than native CUDA/Metal paths",
        "Flash Attention was CUDA-only until 2024; ROCm/Metal ports now exist but lag in features",
        "Portability stack: llama.cpp + GGUF works across CUDA, ROCm, Metal, Vulkan, SYCL, CPU",
    ],
    "dense_vs_moe": [
        "DeepSeek-V3: 671B total, 37B active/token—82.6 HumanEval-Mul, outperforms Llama-3.1-405B on coding",
        "Mixtral 8x7B: 47B total, ~13B active—needs ~26GB VRAM at Q4 but performs like dense 30B+",
        "Dense Qwen2.5-72B at Q4_K_M: ~42GB VRAM with consistent per-token latency",
        "MoE models suffer bandwidth bottlenecks: all expert weights loaded even when only 2 of 8 fire",
        "For single 24GB GPU: dense 7B-14B practical; MoE requires 48GB+ or multi-GPU",
        "Dense models quantize more uniformly; MoE expert layers can degrade unevenly under aggressive quant",
    ],
    "coding_model_traits": [
        "DeepSeek-V3: 42.0% SWE-Bench Verified, 79.7% Aider-Edit, 49.6% Aider-Polyglot—top open-source for practical coding",
        "IF-Eval scores: DeepSeek-V3 86.1%, Qwen2.5-72B 84.1%, Llama-3.1-405B 86.0%—critical for structured output",
        "LiveCodeBench Pass@1: DeepSeek-V3 37.6%, Qwen2.5-72B 28.7%—measures genuine generation vs memorization",
        "Qwen2.5-Coder-32B: 92.7% HumanEval, 90.2% MBPP+—runnable on 2×24GB GPUs at Q4",
        "Aider-Polyglot: DeepSeek-V3 49.6% vs GPT-4o 16.0%—reveals real polyglot codebase handling",
        "Tool-use requires explicit training; Qwen2.5 and Llama-3.1 families have it, base code models often don't",
    ],
    "reasoning_chains": [
        "DeepSeek-R1 (671B MoE): 79.8% AIME 2024, 2029 Codeforces—comparable to OpenAI o1",
        "QwQ-32B: dense 32.5B reasoning model, competitive with R1—most practical CoT for local 24GB GPUs at Q4",
        "R1's CoT boosts LiveCodeBench from 37.6% to 65.9%—75% relative improvement by allowing thinking",
        "R1-Distill-Qwen-32B outperforms o1-mini while being locally runnable—distilled reasoning without MoE",
        "CoT models generate up to 32K thinking tokens/response; 3-10x higher throughput requirements",
        "QwQ-32B requires Temperature=0.6, TopP=0.95—greedy decoding causes infinite repetition loops",
    ],
    "model_selection_for_agents": [
        "24GB VRAM: Qwen2.5-Coder-14B at Q5 (~12GB) or QwQ-32B at Q4 (~20GB)—leaving KV headroom",
        "48GB VRAM: Qwen2.5-Coder-32B at Q5 or DeepSeek-R1-Distill-32B at Q6—near-frontier coding",
        "Token throughput target: >15 tok/s; below 8 tok/s causes timeouts in Aider, SWE-agent, OpenHands",
        "Two-tier architecture: fast 7B for simple edits (40+ tok/s) + QwQ-32B for reasoning (10-15 tok/s)",
        "Q4_K_M loses 1-3% on coding benchmarks vs FP16—acceptable; Q3 shows 5-10% degradation",
        "Monitor VRAM thermal throttling: sustained >90% utilization causes 15-30% throughput loss after 2-4 hours",
    ],
    "agent_context_management": [
        "Claude supports 200K tokens; GPT-4o/Qwen2.5-Coder support 128K—hard ceiling for working memory",
        "Typical 10K-line codebase = 40-60K tokens fully loaded; agents must be selective for 50K+ line repos",
        "Tool definitions consume 500-2000 tokens; 20 tools ≈ 3-5K tokens of fixed overhead per request",
        "Repo-map strategies compress 1M-line codebase into ~15-30K tokens of structural context",
        "KV-cache: 32-layer model with 128K context needs ~8GB VRAM just for cache",
        "Context utilization above 80% degrades quality; stay under 70% for reasoning-heavy tasks",
    ],
    "yolo_coding_mode": [
        "Claude Code auto-accept and Cursor YOLO mode: agents execute 50-200 tool calls autonomously per task",
        "Without guardrails, runaway agent can consume $5-50 in API costs or 10K+ local GPU-seconds",
        "Best practice: auto-commit with git before each modification enables instant rollback",
        "Docker sandbox adds ~200MB RAM overhead but prevents filesystem escape and secret exfiltration",
        "Token budget limits (max 100K output/task) prevent infinite loops; $1-5 cost caps common",
        "Automated test execution after changes: agents catch 70-90% of regressions before human review",
    ],
    "batch_concurrency": [
        "7B Q4: ~4.5GB base + ~2GB per 8K-context request; 24GB GPU handles 3-4 concurrent agents",
        "70B Q4 on dual 24GB (tensor parallel): ~13GB left for KV-cache → 1-2 concurrent 32K agents",
        "vLLM PagedAttention reduces KV waste 60-80%, enabling 2-3x more concurrent requests",
        "CPU offload overflow: 8-16 concurrent agents on 64GB RAM + 24GB VRAM, but 5-15x slower",
        "Continuous batching achieves 80-95% GPU utilization vs 20-40% for sequential processing",
        "Optimal homelab: 2-3 agents on 24GB GPU at 8K context, or 6-8 with aggressive 4K windows",
    ],
    "tool_use_function_calling": [
        "Tool schemas: 100-300 tokens each; 30 tools = 3-9K tokens of context burned on definitions alone",
        "Claude 3.5/GPT-4o: 90%+ multi-tool accuracy; Qwen2.5-72B: 85%+; 7B-13B models: 70-80%",
        "Each tool call round-trip: 1-5 seconds locally, 2-10 seconds via API, compounding over 20-50 calls",
        "Parallel tool calling (Claude, GPT-4o): batch 3-10 independent calls, reducing round-trips 50-70%",
        "Structured output modes reduce malformed responses from 5-15% to under 1% on capable models",
        "Local sweet spot: Qwen2.5-Coder-32B with tool-calling fine-tune—fits 24GB at Q4 with strong accuracy",
    ],
    "vram_tiers_and_gpus": [
        "RTX 4090 (24GB, 1 TB/s): ₹1,60,000-₹1,85,000—runs 34B fully or 70B with partial offload",
        "RTX 5090 (32GB, 1.8 TB/s): ₹2,20,000-₹2,60,000—first consumer card fitting 70B Q4 fully",
        "RTX A6000 (48GB, 768 GB/s): ₹1,20,000-₹1,80,000 used—best value for 70B coding agents",
        "Dual RTX 3090 (2×24GB): ₹70,000-₹90,000 each used—budget king for 48GB combined",
        "16GB GPUs (RTX 4060 Ti 16GB): ₹45,000-₹55,000—minimum viable for useful coding assistance",
        "Apple M4 Max 128GB unified: ₹3,00,000+—competitive inference despite lower raw FLOPS",
    ],
    "ram_bandwidth_for_offload": [
        "DDR4-3200 dual-channel: ~51 GB/s theoretical, ~40-45 real → 2-4 tok/s for heavy 70B offload",
        "DDR5-6000 dual-channel: ~96 GB/s theoretical, ~75-80 real → nearly doubles offload speed vs DDR4",
        "32GB DDR5-6000 kit: ₹8,500-₹12,000; 64GB: ₹16,000-₹22,000; 128GB: ₹45,000-₹65,000 in India",
        "Threadripper PRO quad-channel DDR5: 150-200 GB/s → 8-12 tok/s on fully CPU-offloaded 70B",
        "64GB RAM + 24GB VRAM handles 70B Q4; 128GB + 24GB enables 120B+ with heavy offload",
        "Memory latency (CL30 vs CL40) has <5% impact vs bandwidth—prioritize MHz over timings",
    ],
    "pcie_lanes_multi_gpu": [
        "PCIe 4.0 x16: 32 GB/s each direction; PCIe 5.0 x16: 64 GB/s—most consumer GPUs use 4.0",
        "Consumer Intel (LGA 1700/1851): 16+4 lanes; dual GPUs means x8/x8 split, halving bandwidth",
        "AMD AM5 X670E: some boards support true x16/x16 bifurcation for full-bandwidth dual-GPU",
        "Threadripper PRO: 128 PCIe lanes → 4× GPUs at full x16 each; ₹1,50,000-₹3,00,000 for CPU+board",
        "Inference tensor parallel: x8 vs x16 costs 10-20% performance; pipeline parallel impact under 5%",
        "Budget tip: 2× RTX 3090 on x8/x8 still beats single 4090 for 70B+ due to combined 48GB VRAM",
    ],
    "ssd_weight_loading": [
        "70B Q4 (~40GB): loads in ~6s on Gen4 (7 GB/s), ~11s on Gen3, ~73s on SATA SSD",
        "NVMe Gen4 1TB (Samsung 990 Pro): ₹7,000-₹9,000; Gen3 1TB: ₹5,000-₹6,500 in India",
        "Gen5 (12-14 GB/s): ₹12,000-₹18,000/TB—diminishing returns as RAM copy becomes bottleneck",
        "2TB NVMe ideal for 24/7: stores 8-10 quantized 70B models; costs ₹12,000-₹16,000",
        "GPUDirect Storage: load weights directly to VRAM, 30-40% faster—Linux + recent NVIDIA drivers",
        "Consumer drive endurance fine for 24/7 inference (read-heavy); enterprise NVMe unnecessary",
    ],
    "power_thermals_noise": [
        "RTX 4090 inference draw: 300-350W; undervolting to 0.9V reduces to 250-280W with <5% perf loss",
        "Electricity in India: ₹8-₹12/unit; 600W system 24/7 costs ₹3,500-₹5,200/month",
        "1000VA UPS (₹8,000-₹12,000) essential in India for voltage fluctuations and power cuts",
        "GPU temp target: <80°C; deshrouding with Noctua fans gives 10-15°C drop and much lower noise",
        "Stock 4090 at 70% fan: 48-52 dBA; deshrouded with Noctua NF-A12x25: 35-38 dBA",
        "Idle inference (waiting): ~110-140W total; average for agent use: 150-250W → ₹900-₹1,800/month",
        "AC cooling in Indian summers: adds ₹2,000-₹4,000/month; total homelab ops ₹5,000-₹10,000/month",
    ],
    "os_runtime_friction": [
        "Linux + NVIDIA: install nvidia-driver-550+ and nvidia-container-toolkit for Docker GPU passthrough",
        "WSL2 overhead: ~300-500MB VRAM consumed; CUDA version locked to Windows host driver",
        "Docker on Linux sees full GPU memory; Docker Desktop on Windows adds 1-2GB RAM overhead",
        "llama.cpp, vLLM, TGI publish first-party Linux Docker images; Windows images community-maintained",
        "Apple Silicon unified memory: loads larger models but 2-4x slower than equivalent NVIDIA GPU",
        "For 24/7 agents: Linux bare-metal eliminates driver mismatches, OOM kills, sleep/wake GPU resets",
    ],
    "lora_qlora_basics": [
        "LoRA rank r=16-32 is sweet spot for code fine-tuning; r=8 minimal (1-4MB adapter), r=64 rich (50-200MB)",
        "QLoRA VRAM: 7B needs ~6GB (vs 28GB full fine-tune); 13B needs ~12GB; 34B needs ~24GB",
        "Training 10K examples on 7B: 1-3 hours on RTX 4090 (3 epochs, lr=2e-4, batch_size=4)",
        "Key params: lora_alpha=2×rank, target_modules=[q,v,k,o_proj], dropout=0.05",
        "Tools: unsloth provides 2x speedup/60% less memory; axolotl and TRL/PEFT are standard frameworks",
        "vLLM supports hot-loading multiple LoRA adapters on one base model simultaneously",
    ],
    "when_to_finetune": [
        "Prompt engineering first: handles 70-80% of formatting/behavior needs at zero training cost",
        "Choose RAG when: knowledge updates frequently, needs source citation, corpus exceeds context window",
        "Choose fine-tuning when: consistent output format saves 500-2000 tokens/request, or domain-specific reasoning needed",
        "Decision metric: if prompt engineering achieves >85% accuracy, fine-tuning adds only 5-10%",
        "Minimum viable dataset: 500-1000 high-quality pairs for behavior; 5000-10000 for knowledge",
        "Anti-pattern: fine-tuning for factual knowledge (use RAG)—models hallucinate fine-tuned facts more than retrieved context",
    ],
}

# External reference links for each topic — "Read More" in side panel
TOPIC_LINKS = {
    "tokens_and_parameters": [
        ("What are tokens? (OpenAI)", "https://platform.openai.com/tokenizer"),
        ("Transformer math 101", "https://blog.eleuther.ai/transformer-math/"),
    ],
    "vram_calculation": [
        ("LLM VRAM Calculator", "https://huggingface.co/spaces/NyxKrage/LLM-Model-VRAM-Calculator"),
        ("Can I run this model?", "https://huggingface.co/spaces/Vokturz/can-it-run-llm"),
    ],
    "context_window_math": [
        ("KV Cache explained", "https://magazine.sebastianraschka.com/p/understanding-large-language-models"),
        ("GQA paper (Ainslie et al.)", "https://arxiv.org/abs/2305.13245"),
    ],
    "prefill_vs_decode": [
        ("LLM inference explained", "https://www.databricks.com/blog/llm-inference-performance-engineering-best-practices"),
        ("Prefill vs Decode deep-dive", "https://developer.nvidia.com/blog/mastering-llm-techniques-inference-optimization/"),
    ],
    "kv_cache_growth": [
        ("Efficient KV cache management", "https://arxiv.org/abs/2309.06180"),
        ("PagedAttention paper (vLLM)", "https://arxiv.org/abs/2309.06180"),
    ],
    "memory_bandwidth_vs_compute": [
        ("Roofline model for LLMs", "https://arxiv.org/abs/2402.14848"),
        ("GPU bandwidth benchmarks", "https://www.techpowerup.com/gpu-specs/"),
    ],
    "latency_vs_throughput": [
        ("LLM serving tradeoffs", "https://www.anyscale.com/blog/continuous-batching-llm-inference"),
        ("Batch inference guide", "https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html"),
    ],
    "quantization_basics": [
        ("Quantization overview (HuggingFace)", "https://huggingface.co/docs/optimum/concept_guides/quantization"),
        ("GGML quant comparison", "https://github.com/ggerganov/llama.cpp/discussions/2094"),
    ],
    "gguf_formats": [
        ("GGUF format spec", "https://github.com/ggerganov/ggml/blob/master/docs/gguf.md"),
        ("Quant quality comparison", "https://github.com/ggerganov/llama.cpp/pull/1684"),
        ("TheBloke's quant guide", "https://huggingface.co/TheBloke"),
    ],
    "exl2_awq_gptq": [
        ("ExLlamaV2 repo", "https://github.com/turboderp/exllamav2"),
        ("AWQ paper", "https://arxiv.org/abs/2306.00978"),
        ("GPTQ paper", "https://arxiv.org/abs/2210.17323"),
    ],
    "offloading_gpu_cpu_disk": [
        ("llama.cpp GPU offloading", "https://github.com/ggerganov/llama.cpp#using-gpu"),
        ("Offloading performance guide", "https://github.com/ggerganov/llama.cpp/discussions/4167"),
    ],
    "moe_expert_routing": [
        ("Mixtral paper", "https://arxiv.org/abs/2401.04088"),
        ("DeepSeek MoE paper", "https://arxiv.org/abs/2401.06066"),
        ("MoE explained (HuggingFace)", "https://huggingface.co/blog/moe"),
    ],
    "speculative_decoding": [
        ("Speculative sampling paper", "https://arxiv.org/abs/2302.01318"),
        ("Medusa: multi-head decoding", "https://arxiv.org/abs/2401.10774"),
        ("EAGLE speculative decoding", "https://arxiv.org/abs/2401.15077"),
    ],
    "llama_cpp": [
        ("llama.cpp GitHub", "https://github.com/ggerganov/llama.cpp"),
        ("llama.cpp server docs", "https://github.com/ggerganov/llama.cpp/blob/master/examples/server/README.md"),
        ("Ollama (llama.cpp wrapper)", "https://ollama.com/"),
    ],
    "ktransformers": [
        ("KTransformers GitHub", "https://github.com/kvcache-ai/ktransformers"),
        ("KTransformers tutorial", "https://kvcache-ai.github.io/ktransformers/"),
    ],
    "vllm_tensorrt": [
        ("vLLM docs", "https://docs.vllm.ai/"),
        ("vLLM GitHub", "https://github.com/vllm-project/vllm"),
        ("TensorRT-LLM GitHub", "https://github.com/NVIDIA/TensorRT-LLM"),
    ],
    "mlx_apple_silicon": [
        ("MLX GitHub", "https://github.com/ml-explore/mlx"),
        ("mlx-lm models", "https://github.com/ml-explore/mlx-examples/tree/main/llms"),
        ("MLX community models", "https://huggingface.co/mlx-community"),
    ],
    "runtime_compat": [
        ("CUDA toolkit", "https://developer.nvidia.com/cuda-toolkit"),
        ("ROCm compatibility", "https://rocm.docs.amd.com/"),
        ("llama.cpp backend guide", "https://github.com/ggerganov/llama.cpp#build"),
    ],
    "dense_vs_moe": [
        ("DeepSeek-V3 paper", "https://arxiv.org/abs/2412.19437"),
        ("Switch Transformer (MoE intro)", "https://arxiv.org/abs/2101.03961"),
    ],
    "coding_model_traits": [
        ("SWE-bench leaderboard", "https://www.swebench.com/"),
        ("LiveCodeBench", "https://livecodebench.github.io/"),
        ("Aider LLM leaderboards", "https://aider.chat/docs/leaderboards/"),
    ],
    "reasoning_chains": [
        ("DeepSeek-R1 paper", "https://arxiv.org/abs/2501.12948"),
        ("QwQ-32B model card", "https://huggingface.co/Qwen/QwQ-32B"),
        ("Chain-of-thought prompting", "https://arxiv.org/abs/2201.11903"),
    ],
    "model_selection_for_agents": [
        ("Aider model recommendations", "https://aider.chat/docs/llms.html"),
        ("Open LLM Leaderboard", "https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard"),
        ("LocalLLaMA subreddit", "https://www.reddit.com/r/LocalLLaMA/"),
    ],
    "agent_context_management": [
        ("Aider repo-map strategy", "https://aider.chat/docs/repomap.html"),
        ("RAG for code (Cursor)", "https://cursor.sh/blog"),
        ("Embedding-based retrieval", "https://docs.llamaindex.ai/"),
    ],
    "yolo_coding_mode": [
        ("Claude Code docs", "https://docs.anthropic.com/en/docs/claude-code"),
        ("Aider auto-commits", "https://aider.chat/docs/git.html"),
        ("SWE-agent", "https://github.com/princeton-nlp/SWE-agent"),
    ],
    "batch_concurrency": [
        ("vLLM continuous batching", "https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html"),
        ("Multi-agent orchestration", "https://github.com/langchain-ai/langgraph"),
    ],
    "tool_use_function_calling": [
        ("Function calling guide (OpenAI)", "https://platform.openai.com/docs/guides/function-calling"),
        ("Qwen tool calling", "https://qwen.readthedocs.io/en/latest/framework/function_call.html"),
        ("ToolBench dataset", "https://github.com/OpenBMB/ToolBench"),
    ],
    "vram_tiers_and_gpus": [
        ("GPU specs database", "https://www.techpowerup.com/gpu-specs/"),
        ("RTX 4090 vs A6000 for LLMs", "https://www.reddit.com/r/LocalLLaMA/"),
        ("India GPU prices (MDComputers)", "https://mdcomputers.in/graphics-card"),
    ],
    "ram_bandwidth_for_offload": [
        ("DDR5 bandwidth explained", "https://www.crucial.com/articles/about-memory/difference-between-ddr4-and-ddr5-ram"),
        ("Memory benchmark tool", "https://www.aida64.com/"),
    ],
    "pcie_lanes_multi_gpu": [
        ("PCIe bandwidth explained", "https://www.trentonsystems.com/en-us/resource-hub/blog/pcie-gen4-vs-gen5"),
        ("Multi-GPU LLM setup guide", "https://www.reddit.com/r/LocalLLaMA/wiki/"),
    ],
    "ssd_weight_loading": [
        ("NVMe Gen4 vs Gen5 benchmarks", "https://www.tomshardware.com/reviews/best-ssds,3891.html"),
        ("GPUDirect Storage", "https://developer.nvidia.com/gpudirect-storage"),
    ],
    "power_thermals_noise": [
        ("GPU undervolting guide", "https://www.techpowerup.com/review/nvidia-geforce-rtx-4090-undervolt/"),
        ("Noctua deshroud mod", "https://www.reddit.com/r/sffpc/comments/deshroud/"),
        ("India electricity tariffs", "https://www.bijlibachao.com/"),
    ],
    "os_runtime_friction": [
        ("NVIDIA container toolkit", "https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/"),
        ("WSL2 GPU setup", "https://learn.microsoft.com/en-us/windows/ai/directml/gpu-cuda-in-wsl"),
        ("Docker GPU passthrough", "https://docs.docker.com/config/containers/resource_constraints/#gpu"),
    ],
    "lora_qlora_basics": [
        ("QLoRA paper", "https://arxiv.org/abs/2305.14314"),
        ("Unsloth (fast fine-tuning)", "https://github.com/unslothai/unsloth"),
        ("Axolotl training tool", "https://github.com/OpenAccess-AI-Collective/axolotl"),
    ],
    "when_to_finetune": [
        ("Fine-tuning vs RAG decision", "https://www.anyscale.com/blog/fine-tuning-is-for-form-not-facts"),
        ("RAG vs fine-tuning guide", "https://docs.llamaindex.ai/en/stable/"),
        ("PEFT library (HuggingFace)", "https://huggingface.co/docs/peft/"),
    ],
}


def _init_knowledge_state() -> dict:
    """Initialize a fresh knowledge state with all topics unseen."""
    topics = {}
    for tid, tinfo in TOPIC_DAG.items():
        topics[tid] = {
            "status": "unseen",
            "confidence": 0.0,
            "last_taught": None,
            "key_facts": [],
            "reliability": "stable",
        }
    return {
        "version": 1,
        "goals": [g["id"] for g in USER_GOALS],
        "topics": topics,
        "curriculum_position": {
            "current_layer": 0,
            "completed_layers": [],
            "recent_detours": [],
        },
        "applied_insights": [],
        "total_lessons_completed": 0,
    }


def _get_next_topics(knowledge_state: dict, count: int = 5) -> list[str]:
    """Get the next topics to teach based on DAG prerequisites.
    
    Returns up to `count` topics whose prerequisites are all met
    (status >= 'introduced'), prioritized by layer then goal relevance.
    """
    topics = knowledge_state.get("topics", {})
    ready = []
    for tid, tinfo in TOPIC_DAG.items():
        ts = topics.get(tid, {})
        if ts.get("status", "unseen") != "unseen":
            continue  # already taught
        # Check all prereqs are at least introduced
        prereqs_met = True
        for prereq in tinfo.get("prereqs", []):
            ps = topics.get(prereq, {}).get("status", "unseen")
            if ps == "unseen":
                prereqs_met = False
                break
        if prereqs_met:
            ready.append(tid)
    
    # Sort by layer (lower first), then by number of goal tags (more = higher priority)
    ready.sort(key=lambda t: (TOPIC_DAG[t]["layer"], -len(TOPIC_DAG[t].get("goal_tags", []))))
    return ready[:count]


def _get_knowledge_summary(knowledge_state: dict) -> str:
    """Get a compact summary of what has been learned so far."""
    topics = knowledge_state.get("topics", {})
    learned = []
    for tid, ts in topics.items():
        if ts.get("status") in ("introduced", "reinforced", "applied"):
            facts = ts.get("key_facts", [])
            title = TOPIC_DAG.get(tid, {}).get("title", tid)
            if facts:
                learned.append(f"{title}: {'; '.join(facts[:2])}")
            else:
                learned.append(title)
    if not learned:
        return "No topics learned yet — complete beginner."
    return "Topics learned: " + " | ".join(learned)


def _generate_learner_context(knowledge_state: dict, target_prompt: str) -> str:
    """Generate a compact, task-specific learner context for a given prompt category.
    
    Returns max ~150 tokens of relevant context based on what the user knows
    and what's relevant to the target prompt.
    """
    topics = knowledge_state.get("topics", {})
    
    # Collect relevant facts based on target prompt
    relevant_topic_ids = {
        "hardware": ["vram_calculation", "vram_tiers_and_gpus", "ram_bandwidth_for_offload",
                      "pcie_lanes_multi_gpu", "power_thermals_noise", "os_runtime_friction",
                      "memory_bandwidth_vs_compute", "offloading_gpu_cpu_disk"],
        "models_and_agents": ["dense_vs_moe", "coding_model_traits", "reasoning_chains",
                               "model_selection_for_agents", "quantization_basics",
                               "agent_context_management", "yolo_coding_mode"],
        "efficiency_research": ["quantization_basics", "gguf_formats", "exl2_awq_gptq",
                                 "offloading_gpu_cpu_disk", "moe_expert_routing",
                                 "speculative_decoding", "llama_cpp", "ktransformers",
                                 "memory_bandwidth_vs_compute", "kv_cache_growth"],
        "deals_and_blogs": ["vram_calculation", "vram_tiers_and_gpus", "quantization_basics",
                             "ram_bandwidth_for_offload"],
        "learning_feed": [],  # learning prompt builds its own context
        "model_benchmarks": ["coding_model_traits", "dense_vs_moe", "quantization_basics",
                              "latency_vs_throughput"],
        "recommendation": ["vram_calculation", "quantization_basics", "offloading_gpu_cpu_disk",
                            "model_selection_for_agents", "llama_cpp", "ktransformers",
                            "vram_tiers_and_gpus", "ram_bandwidth_for_offload"],
    }
    
    target_topics = relevant_topic_ids.get(target_prompt, [])
    if not target_topics:
        return ""
    
    known_facts = []
    active_learning = []
    gaps = []
    
    for tid in target_topics:
        ts = topics.get(tid, {})
        status = ts.get("status", "unseen")
        title = TOPIC_DAG.get(tid, {}).get("title", tid.replace("_", " ").title())
        
        if status in ("introduced", "reinforced", "applied"):
            facts = ts.get("key_facts", [])
            if facts:
                known_facts.extend(facts[:2])
            elif status == "introduced":
                active_learning.append(title)
        else:
            gaps.append(title)
    
    parts = []
    if known_facts:
        parts.append(f"User knows: {'; '.join(known_facts[:5])}")
    if active_learning:
        parts.append(f"Currently learning: {', '.join(active_learning[:2])}")
    if gaps and target_prompt == "recommendation":
        parts.append(f"Knowledge gaps blocking action: {', '.join(gaps[:3])}")
    
    if not parts:
        parts.append("User is a beginner — explain concepts simply, don't assume prior knowledge.")
    
    return " ".join(parts)


def _build_learning_prompt_context(knowledge_state: dict, analytical_state: dict = None) -> str:
    """Build the dynamic context that gets injected into the learning_feed prompt.
    
    Tells the AI what topics to teach next, what's already been covered,
    and what mode to operate in (curriculum vs latest-developments).
    Includes recent analytical findings for grounded lessons.
    """
    topics = knowledge_state.get("topics", {})
    total = len(TOPIC_DAG)
    learned_count = sum(1 for t in topics.values() if t.get("status") != "unseen")
    
    # Build analytical context from research findings
    research_context = ""
    if analytical_state:
        snippets = []
        for domain, ds in analytical_state.items():
            analysis = (ds.get("current_analysis", "") or "")[:150]
            if analysis:
                snippets.append(f"[{domain}] {analysis}")
        if snippets:
            research_context = (
                "\n\nRECENT RESEARCH FINDINGS (use these real data points in lessons):\n"
                + "\n".join(snippets[:4])
                + "\nReference these findings when teaching relevant concepts. "
                "Use actual model names, prices, and benchmarks from above.\n"
            )
    
    # Determine mode
    all_introduced = all(
        topics.get(tid, {}).get("status", "unseen") != "unseen"
        for tid in TOPIC_DAG
    )
    
    if all_introduced:
        # Latest developments mode
        summary = _get_knowledge_summary(knowledge_state)
        return (
            f"MODE: LATEST DEVELOPMENTS. The learner has completed the foundational curriculum ({learned_count}/{total} topics). "
            f"{summary} "
            "Now teach 3-5 NEW developments from the past 7 days: new model releases, new inference techniques, "
            "new quantization methods, new benchmarks, new tools — that build on what the learner already knows. "
            "Each lesson should connect the new development to existing knowledge. "
            "Search Reddit r/LocalLLaMA, Hacker News, YouTube, and tech blogs for the latest. "
            "Use the same topic_id format but prefix with 'latest_' (e.g., 'latest_ktransformers_v7'). "
            + research_context
        )
    
    # Curriculum mode
    next_topics = _get_next_topics(knowledge_state, count=5)
    if not next_topics:
        # Edge case: all done
        return "MODE: LATEST DEVELOPMENTS. All foundational topics covered. Teach latest developments. "
    
    # Build context about what's already known
    known_summary = _get_knowledge_summary(knowledge_state)
    
    # Build the teaching request
    topic_list = []
    for tid in next_topics:
        tinfo = TOPIC_DAG[tid]
        prereqs = tinfo.get("prereqs", [])
        prereq_titles = [TOPIC_DAG.get(p, {}).get("title", p) for p in prereqs]
        prereq_str = f" (builds on: {', '.join(prereq_titles)})" if prereq_titles else " (no prerequisites)"
        topic_list.append(f"- topic_id: '{tid}' — {tinfo['title']}{prereq_str}")
    
    topics_str = "\n".join(topic_list)
    
    return (
        f"MODE: CURRICULUM (progress: {learned_count}/{total} topics complete). "
        f"{known_summary} "
        f"CRITICAL: You MUST return EXACTLY {len(next_topics)} lessons in the 'lessons' array — one per topic listed below. "
        f"Do NOT merge topics. Do NOT skip any. Each gets its own lesson object.\n"
        f"TEACH THESE {len(next_topics)} TOPICS:\n{topics_str}\n"
        "The learner is building knowledge progressively to set up a local LLM coding rig in India. "
        "Budget ₹1.5-3.5L. Goal: run 30B-70B coding models at 25+ tok/s for 24/7 YOLO agents. "
        "Make each lesson build on the previous ones. Use real numbers, real models, real hardware. "
        "Search the web for the latest data and examples. "
        + research_context
    )


def _update_knowledge_state(knowledge_state: dict, learning_response: dict) -> dict:
    """Update knowledge state based on learning prompt results.
    
    Processes lessons from the response, updates topic statuses,
    records key facts, and advances curriculum position.
    """
    topics = knowledge_state.get("topics", {})
    lessons = learning_response.get("lessons", [])
    
    # Handle case where AI returns flat dict instead of lessons array
    if not lessons and isinstance(learning_response, dict):
        # Case 1: The response itself IS a single lesson (has topic_id at top level)
        if "topic_id" in learning_response:
            lessons = [learning_response]
        else:
            # Case 2: Dict of lesson objects keyed by name
            for key, val in learning_response.items():
                if isinstance(val, dict) and "topic_id" in val:
                    lessons.append(val)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict) and "topic_id" in item:
                            lessons.append(item)
    
    today = datetime.now().strftime("%Y-%m-%d")
    lessons_completed = 0
    
    for lesson in lessons:
        if not isinstance(lesson, dict):
            continue
        tid = lesson.get("topic_id", "")
        if not tid:
            continue
        
        # Get or create topic state
        if tid not in topics:
            # Could be a "latest_" topic in developments mode
            topics[tid] = {
                "status": "unseen",
                "confidence": 0.0,
                "last_taught": None,
                "key_facts": [],
                "reliability": "stable",
            }
        
        ts = topics[tid]
        old_status = ts.get("status", "unseen")
        
        # Advance status
        status_order = ["unseen", "introduced", "reinforced", "applied"]
        if old_status in status_order:
            idx = status_order.index(old_status)
            new_idx = min(idx + 1, len(status_order) - 1)
            ts["status"] = status_order[new_idx]
        
        ts["last_taught"] = today
        ts["confidence"] = min(1.0, ts.get("confidence", 0) + 0.4)
        
        # Record key facts (deduplicate)
        new_facts = lesson.get("key_takeaways", [])
        existing = set(ts.get("key_facts", []))
        for fact in new_facts:
            if isinstance(fact, str) and fact not in existing:
                ts["key_facts"].append(fact)
                existing.add(fact)
        # Keep max 5 facts per topic
        ts["key_facts"] = ts["key_facts"][:5]
        
        # Record reliability
        ts["reliability"] = lesson.get("reliability", "stable")
        
        # Store full lesson history (keep last 3 lessons per topic)
        if "lessons" not in ts:
            ts["lessons"] = []
        ts["lessons"].append({
            "title": lesson.get("title", ""),
            "content": lesson.get("content", "")[:2000],
            "key_takeaways": new_facts[:5],
            "prerequisite_recap": lesson.get("prerequisite_recap", ""),
            "practical_exercise": lesson.get("practical_exercise", ""),
            "answer": lesson.get("answer", ""),
            "hardware_implication": lesson.get("hardware_implication", ""),
            "resources": lesson.get("resources", [])[:5],
            "date": today,
        })
        ts["lessons"] = ts["lessons"][-3:]  # keep last 3
        
        lessons_completed += 1
    
    knowledge_state["topics"] = topics
    knowledge_state["total_lessons_completed"] = knowledge_state.get("total_lessons_completed", 0) + lessons_completed
    
    # Update curriculum position
    learned_count = sum(1 for t in topics.values() if t.get("status") != "unseen")
    knowledge_state["curriculum_position"]["current_layer"] = max(
        (TOPIC_DAG.get(tid, {}).get("layer", 0) 
         for tid, ts in topics.items() 
         if ts.get("status") != "unseen"),
        default=0
    )
    
    # Track completed layers
    completed_layers = set()
    for layer in range(8):
        layer_topics = [tid for tid, t in TOPIC_DAG.items() if t["layer"] == layer]
        if layer_topics and all(topics.get(tid, {}).get("status", "unseen") != "unseen" for tid in layer_topics):
            completed_layers.add(layer)
    knowledge_state["curriculum_position"]["completed_layers"] = sorted(completed_layers)
    
    logger.info(f"Knowledge state updated: {lessons_completed} lessons, {learned_count}/{len(TOPIC_DAG)} topics covered")
    
    return knowledge_state


# ─── Gather Prompts (v2 Pipeline: focused data collection) ───────────────────

def _build_gather_context(state: dict, domain: str) -> str:
    """Build a compact summary of what we already know for a gather domain.
    
    Gives the gatherer context so it can focus on genuinely NEW information.
    Returns max ~150 words of previous-state summary.
    """
    analytical = state.get("analytical_state", {})
    domain_state = analytical.get(domain, {})
    last_run = state.get("last_run", "never")
    
    # Summarize existing evidence (max 5 claims)
    evidence = domain_state.get("evidence", [])
    known_claims = [e.get("claim", "")[:80] for e in evidence[:5]]
    
    context_parts = [f"Last analysis: {last_run}."]
    if domain_state.get("current_analysis"):
        # Truncate to ~100 words
        analysis = domain_state["current_analysis"]
        words = analysis.split()[:100]
        context_parts.append(f"Current understanding: {' '.join(words)}")
    elif known_claims:
        context_parts.append(f"Known: {'; '.join(known_claims)}")
    
    confidence = domain_state.get("confidence", 0)
    context_parts.append(f"Confidence: {confidence:.0%}.")
    
    return " ".join(context_parts)


GATHER_PROMPTS = {
    "hardware_gather": (
        "You are a hardware market researcher for local LLM inference. Today is {date}. "
        "CONTEXT: {gather_context} "
        "YOUR TASK: Search the web for NEW hardware information since last analysis. "
        "Focus areas: "
        "- Apple Mac Studio/Mac Mini (M4 Max, M5 rumors) availability and pricing in India & US "
        "- AMD Strix Halo 128GB mini PCs (Framework, Bosgame, Beelink, Minisforum, Corsair) "
        "- NVIDIA RTX 5090/5080 availability and pricing in India "
        "- Any NEW product with >=48GB unified memory or >=24GB VRAM under $5000/INR 5L "
        "- Apple events, WWDC announcements "
        "Return ONLY a JSON object with this EXACT structure: "
        '{{"findings": ['
        '{{"claim": "specific factual finding (e.g., Mac Studio M4 Max 128GB now orderable on apple.com/in at INR 4,19,900)", '
        '"source": "where you found this (e.g., apple.com/in product page)", '
        '"source_type": "retailer|benchmark|community|official|blog|review", '
        '"date": "{date}", '
        '"confidence_signal": "direct_observation|multiple_reports|single_anecdote|official_announcement", '
        '"price": "price if applicable (e.g., INR 4,19,900 or $4,999)", '
        '"related_domains": ["models", "optimization"]}}'
        '], '
        '"nothing_new": false, '
        '"summary": "1-2 sentence summary of what changed since last analysis"'
        "}} "
        "Include 5-15 findings. Each finding should be a SPECIFIC, VERIFIABLE claim. "
        "If nothing changed since last analysis, set nothing_new=true and return empty findings. "
        "DO NOT analyze or recommend. Just gather raw facts. Return ONLY the JSON."
    ),

    "models_gather": (
        "You are a model ecosystem researcher for local LLM coding. Today is {date}. "
        "CONTEXT: {gather_context} "
        "YOUR TASK: Search the web for NEW model information since last analysis. "
        "Focus areas: "
        "- New coding-capable model releases (any size that fits 48-128GB unified or 24-32GB VRAM) "
        "- Benchmark results (HumanEval, SWE-bench, LiveCodeBench, Aider polyglot) "
        "- Architecture innovations (MoE, hybrid, new attention mechanisms) "
        "- Inference speed benchmarks on target hardware (tok/s on M4 Max, Strix Halo, RTX 5090) "
        "- Quantization format support (GGUF, EXL2, AWQ variants) "
        "- Coding agent frameworks supporting local models "
        "Return ONLY a JSON object with this EXACT structure: "
        '{{"findings": ['
        '{{"claim": "specific factual finding (e.g., Qwen3-32B released May 2026 scoring 78.5 on HumanEval+)", '
        '"source": "where you found this (e.g., huggingface.co/Qwen release page)", '
        '"source_type": "retailer|benchmark|community|official|blog|review|paper", '
        '"date": "{date}", '
        '"confidence_signal": "direct_observation|multiple_reports|single_anecdote|official_announcement", '
        '"benchmark_data": {{"metric": "value"}}, '
        '"related_domains": ["hardware", "optimization"]}}'
        '], '
        '"nothing_new": false, '
        '"summary": "1-2 sentence summary of what changed since last analysis"'
        "}} "
        "Include 5-15 findings. Each finding should be a SPECIFIC, VERIFIABLE claim with numbers. "
        "If nothing changed, set nothing_new=true. "
        "DO NOT analyze or recommend. Just gather raw facts. Return ONLY the JSON."
    ),

    "efficiency_gather": (
        "You are an LLM optimization researcher. Today is {date}. "
        "CONTEXT: {gather_context} "
        "YOUR TASK: Search the web for NEW optimization and efficiency information since last analysis. "
        "Focus areas: "
        "- Inference engine updates (llama.cpp, vLLM, KTransformers, exllamav2, MLX, Ollama, SGLang) "
        "- Quantization advances (new formats, quality improvements, VRAM savings) "
        "- MoE offloading improvements (CPU/GPU split, expert routing efficiency) "
        "- Memory optimization (KV cache compression, flash attention, paged attention) "
        "- Speculative decoding advances "
        "- Budget hardware optimization configs (running 30B+ on 16GB VRAM) "
        "- Performance tricks from community (r/LocalLLaMA, GitHub issues/PRs) "
        "Return ONLY a JSON object with this EXACT structure: "
        '{{"findings": ['
        '{{"claim": "specific factual finding (e.g., llama.cpp b4532 adds 25% speedup for MoE models via new expert scheduling)", '
        '"source": "where you found this (e.g., github.com/ggerganov/llama.cpp/releases)", '
        '"source_type": "retailer|benchmark|community|official|blog|review|github", '
        '"date": "{date}", '
        '"confidence_signal": "direct_observation|multiple_reports|single_anecdote|official_announcement", '
        '"performance_data": "quantitative improvement if available", '
        '"related_domains": ["hardware", "models"]}}'
        '], '
        '"nothing_new": false, '
        '"summary": "1-2 sentence summary of what changed since last analysis"'
        "}} "
        "Include 5-15 findings. Focus on QUANTITATIVE improvements (tok/s gains, VRAM reductions, etc). "
        "Signal levels: breakthrough (2x+ or enables new configs), notable (10-50% improvement), incremental (<10%). "
        "DO NOT analyze or recommend. Just gather raw facts. Return ONLY the JSON."
    ),

    "community_gather": (
        "You are a community intelligence researcher for local LLM. Today is {date}. "
        "CONTEXT: {gather_context} "
        "YOUR TASK: Search the web for NEW community discussions, real-world setups, and practical insights. "
        "Focus areas: "
        "- r/LocalLLaMA top posts (last 7 days) about running 30B-70B models "
        "- YouTube demos/reviews of local LLM setups "
        "- GitHub repos: new tools, configs, or scripts for local inference "
        "- Real-world user benchmarks on target hardware (M4 Max, Strix Halo, RTX 5090, budget GPUs) "
        "- Deals, price drops, stock alerts for target hardware in India "
        "- Fine-tuning experiences: what works for coding tasks "
        "- Practical agent setups: what models + frameworks people actually use for YOLO coding "
        "Return ONLY a JSON object with this EXACT structure: "
        '{{"findings": ['
        '{{"claim": "specific community finding (e.g., Reddit user reports Qwen3-30B-A3B at 180 tok/s on M4 Max 128GB with llama.cpp b4530)", '
        '"source": "where you found this (e.g., reddit.com/r/LocalLLaMA/... post title)", '
        '"source_type": "community|reddit|youtube|github|forum|discord", '
        '"date": "{date}", '
        '"confidence_signal": "direct_observation|multiple_reports|single_anecdote|verified_benchmark", '
        '"upvotes_or_engagement": "50 upvotes / 2000 views if known", '
        '"related_domains": ["hardware", "models", "optimization"]}}'
        '], '
        '"nothing_new": false, '
        '"summary": "1-2 sentence summary of community buzz since last analysis"'
        "}} "
        "Include 5-15 findings. Prioritize HIGH-ENGAGEMENT posts (many upvotes/comments). "
        "Include real-world benchmark numbers when people share them. "
        "DO NOT analyze or recommend. Just gather what the community is saying. Return ONLY the JSON."
    ),
}

# Expected keys for gather prompt validation
GATHER_EXPECTED_KEYS = {"findings", "nothing_new", "summary"}


# ─── Monitoring Prompts (legacy, used as fallback) ────────────────────────────

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
        "You are a progressive LLM learning tutor. Today is {date}. "
        "{learning_context}"
        "Return ONLY a JSON object (no markdown fences, no explanation) with this EXACT structure: "
        '{{"lessons": ['
        '{{"topic_id": "the_topic_id", '
        '"title": "Human-readable lesson title", '
        '"content": "500-800 word lesson that builds on prerequisites. Explain clearly for a beginner. '
        'Include concrete examples with real numbers (e.g., actual VRAM calculations, actual tok/s measurements). '
        'Reference specific models, tools, or hardware when relevant.", '
        '"key_takeaways": ["fact 1", "fact 2", "fact 3"], '
        '"prerequisite_recap": "1-2 sentence recap of what was learned previously that this builds on", '
        '"practical_exercise": "A calculation or decision exercise the learner can try", '
        '"answer": "The answer to the exercise with reasoning", '
        '"hardware_implication": "How this knowledge affects hardware buying decisions", '
        '"resources": [{{"title": "Resource name", "url": "https://...", "type": "article|video|docs|tool|paper"}}, ...], '
        '"reliability": "stable|emerging|experimental"}}'
        '], '
        '"learner_contexts": {{'
        '"hardware": "1 sentence: what the user now knows that is relevant to hardware choices", '
        '"efficiency": "1 sentence: what the user now knows relevant to optimization", '
        '"deals": "1 sentence: what specs the user should look for in deals", '
        '"recommendation": "1 sentence: what the user can now realistically evaluate"}}'
        "}} "
        "IMPORTANT: The 'lessons' array MUST contain one lesson object PER topic listed above. "
        "Do NOT return a single lesson. Return the FULL array. "
        "Cover ALL the listed topics. Each lesson should be thorough and educational. "
        "For 'resources', include 3-5 real URLs (documentation, blog posts, YouTube videos, GitHub repos, papers) "
        "that help the learner go deeper on the topic. Prefer official docs, highly-rated tutorials, and practical guides. "
        "Return ONLY the JSON."
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
    """Load previous monitoring state. Auto-migrates to v2 if needed."""
    if STATE_FILE.exists():
        try:
            state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            # Auto-migrate to v2 analytical pipeline structure
            state = _migrate_state_v2(state)
            return state
        except (json.JSONDecodeError, OSError):
            logger.warning("Could not load state file, starting fresh")
    fresh = {"last_run": None, "checks": {}, "history": []}
    return _migrate_state_v2(fresh)


def save_state(state: dict):
    """Save current monitoring state."""
    STATE_FILE.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")


# ─── State Migration & Pipeline Architecture (v2) ───────────────────────────

def _migrate_state_v2(state: dict) -> dict:
    """Migrate v1 flat state to v2 analytical structure.
    
    V2 adds:
      - analytical_state: per-domain structured analysis with evidence/confidence
      - questions: pending/answered async Q&A
      - changelog: timestamped record of analysis changes
      - pipeline_meta: last run metadata for the multi-stage pipeline
    
    Preserves all existing data — this is additive, not destructive.
    """
    if state.get("state_version", 1) >= 2:
        return state  # already migrated

    logger.info("Migrating state to v2 (analytical pipeline)")

    # Build analytical_state from existing checks + enrichment
    checks = state.get("checks", {})
    enrichment = state.get("enrichment", {})
    recommendation = state.get("recommendation", {})

    analytical_state = {}
    
    # Hardware domain
    hw_evidence = []
    hw_checks = checks.get("hardware", {})
    for key, val in hw_checks.items():
        if isinstance(val, dict) and val.get("info"):
            hw_evidence.append({
                "claim": val["info"],
                "source": "copilot_gather",
                "source_type": "aggregated",
                "date": state.get("last_run", "unknown"),
                "confidence_signal": "single_run",
            })
    analytical_state["hardware"] = {
        "current_analysis": enrichment.get("hardware_deep_dive", ""),
        "options": [],  # will be populated by researcher
        "confidence": 0.6,  # baseline — not yet validated by pipeline
        "evidence": hw_evidence[:20],  # cap at 20 to avoid bloat
        "conflicts_resolved": [],
        "last_changed": state.get("last_run", ""),
        "change_reason": "Initial migration from v1 state",
    }

    # Models domain
    models_evidence = []
    models_checks = checks.get("models_and_agents", {})
    for key, val in models_checks.items():
        if isinstance(val, dict) and val.get("info"):
            models_evidence.append({
                "claim": val["info"],
                "source": "copilot_gather",
                "source_type": "aggregated",
                "date": state.get("last_run", "unknown"),
                "confidence_signal": "single_run",
            })
    analytical_state["models"] = {
        "current_analysis": enrichment.get("model_analysis", ""),
        "options": [],
        "confidence": 0.6,
        "evidence": models_evidence[:20],
        "conflicts_resolved": [],
        "last_changed": state.get("last_run", ""),
        "change_reason": "Initial migration from v1 state",
    }

    # Optimization domain
    eff_evidence = []
    eff_checks = checks.get("efficiency_research", {})
    for key, val in eff_checks.items():
        if isinstance(val, dict) and val.get("info"):
            eff_evidence.append({
                "claim": val["info"],
                "source": "copilot_gather",
                "source_type": "aggregated",
                "date": state.get("last_run", "unknown"),
                "confidence_signal": "single_run",
            })
    analytical_state["optimization"] = {
        "current_analysis": enrichment.get("efficiency_analysis", ""),
        "options": [],
        "confidence": 0.6,
        "evidence": eff_evidence[:20],
        "conflicts_resolved": [],
        "last_changed": state.get("last_run", ""),
        "change_reason": "Initial migration from v1 state",
    }

    # Setup domain (new — no prior data)
    analytical_state["setup"] = {
        "current_analysis": "",
        "options": [],
        "confidence": 0.0,
        "evidence": [],
        "conflicts_resolved": [],
        "last_changed": "",
        "change_reason": "",
    }

    # Set new v2 fields
    state["state_version"] = 2
    state["analytical_state"] = analytical_state
    state["questions"] = {"pending": [], "answered": []}
    state["changelog"] = [{
        "date": datetime.now().strftime("%Y-%m-%d"),
        "domain": "all",
        "change": "Migrated to LLM Homelab v2 analytical pipeline",
        "reason": "Architecture upgrade from monitoring to analysis-first portal",
        "evidence": [],
    }]
    state["pipeline_meta"] = {
        "last_pipeline_run": None,
        "last_gather_results": {},
        "last_critique_status": None,
        "iteration_count": 0,
    }

    logger.info("State migration to v2 complete")
    return state


def _run_gather_parallel(state: dict, today: str) -> dict:
    """Run all gather prompts in parallel using ThreadPoolExecutor.
    
    Uses new GATHER_PROMPTS (v2 pipeline) for data collection, plus legacy
    PROMPTS for categories not yet migrated (learning_feed, model_benchmarks).
    
    Returns dict of {category: {response, parsed}} for each prompt.
    """
    # Mapping from gather prompt names to legacy category names for backward compat
    # The pipeline uses new gather prompts but stores results under legacy keys
    GATHER_TO_LEGACY = {
        "hardware_gather": "hardware",
        "models_gather": "models_and_agents",
        "efficiency_gather": "efficiency_research",
        "community_gather": "deals_and_blogs",
    }
    
    results = {}
    
    def _run_single_gather(category: str) -> tuple:
        """Execute a single gather or legacy prompt. Runs in thread."""
        
        # Check if this is a v2 gather prompt
        if category in GATHER_PROMPTS:
            # Build domain-specific context
            domain_map = {
                "hardware_gather": "hardware",
                "models_gather": "models",
                "efficiency_gather": "optimization",
                "community_gather": "hardware",  # community covers multiple domains
            }
            domain = domain_map.get(category, "hardware")
            gather_ctx = _build_gather_context(state, domain)
            prompt = GATHER_PROMPTS[category].format(date=today, gather_context=gather_ctx)
            
            # Use the gather category name for schema validation (not legacy name)
            response, parsed = run_copilot_with_retry(prompt, category=category)
            
            # Validate/salvage gather format
            if parsed and not _validate_gather_response(parsed):
                # Salvage: single finding object returned at root
                if "claim" in parsed and "source" in parsed:
                    logger.info(f"[GATHER] {category}: salvaging single finding as list")
                    parsed = {"findings": [parsed], "nothing_new": False, "summary": parsed.get("claim", "")}
                # Salvage: list of findings returned without wrapper
                elif isinstance(parsed, list):
                    logger.info(f"[GATHER] {category}: salvaging bare list")
                    parsed = {"findings": parsed, "nothing_new": False, "summary": ""}
                # Salvage: arbitrary data dict — wrap as single finding
                elif isinstance(parsed, dict) and len(parsed) >= 3:
                    logger.info(f"[GATHER] {category}: salvaging arbitrary data as single finding")
                    summary = json.dumps(parsed, default=str)[:200]
                    parsed = {"findings": [{"claim": summary, "source": "copilot_gather", 
                                            "source_type": "ai_analysis", "date": today,
                                            "confidence_signal": "raw_data"}], 
                              "nothing_new": False, "summary": summary}
                else:
                    logger.warning(f"[GATHER] {category}: invalid gather format, passing through")
            
            return category, response, parsed
        
        # Legacy prompts (learning_feed, model_benchmarks)
        prompt_template = PROMPTS.get(category)
        if not prompt_template:
            return category, "", None
            
        if category == "learning_feed":
            ks = state.get("knowledge_state") or _init_knowledge_state()
            analytical_state = state.get("analytical_state", {})
            learning_ctx = _build_learning_prompt_context(ks, analytical_state)
            prompt = prompt_template.format(date=today, learning_context=learning_ctx)
        else:
            prompt = prompt_template.format(date=today)
        
        # Inject dynamic context
        dynamic_context = build_dynamic_prompt_context(state, category=category)
        if dynamic_context:
            prompt = prompt + " " + dynamic_context
        
        # Inject learner context (cross-pollination)
        ks = state.get("knowledge_state")
        if ks and category != "learning_feed":
            learner_ctx = _generate_learner_context(ks, category)
            if learner_ctx:
                prompt = prompt + " LEARNER CONTEXT: " + learner_ctx
        
        response, parsed = run_copilot_with_retry(prompt, category=category)
        return category, response, parsed
    
    # Categories to run: v2 gather prompts + legacy learning + legacy benchmarks
    all_categories = list(GATHER_PROMPTS.keys()) + ["learning_feed", "model_benchmarks"]
    
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(_run_single_gather, cat): cat 
            for cat in all_categories
        }
        
        try:
            for future in as_completed(futures, timeout=600):
                cat = futures[future]
                try:
                    category, response, parsed = future.result()
                    results[category] = {"response": response, "parsed": parsed}
                    if parsed:
                        logger.info(f"[PARALLEL] {category}: success")
                    else:
                        logger.warning(f"[PARALLEL] {category}: parse failed")
                except Exception as e:
                    logger.error(f"[PARALLEL] {cat}: exception - {e}")
                    results[cat] = {"response": "", "parsed": None}
        except TimeoutError:
            logger.warning("[PARALLEL] Some futures timed out, proceeding with partial results")
    
    # Handle any futures that didn't complete within timeout
    for future, cat in futures.items():
        if cat not in results:
            logger.warning(f"[PARALLEL] {cat}: timed out, skipping")
            results[cat] = {"response": "", "parsed": None}
    
    return results


def _validate_gather_response(parsed: dict) -> bool:
    """Validate that a response matches the v2 gather format."""
    if not isinstance(parsed, dict):
        return False
    # Must have 'findings' key (list) and 'nothing_new' (bool)
    if "findings" not in parsed:
        return False
    if not isinstance(parsed["findings"], list):
        return False
    # Each finding should have at least 'claim' and 'source'
    for f in parsed["findings"][:3]:  # check first 3
        if not isinstance(f, dict):
            return False
        if "claim" not in f or "source" not in f:
            return False
    return True


def _convert_gather_to_legacy(gather_results: dict) -> dict:
    """Convert v2 gather results to legacy checks format for backward compat.
    
    The rest of the pipeline (store checks, enrichment, dashboard) still expects
    the old format. This bridges the gap until full migration.
    """
    GATHER_TO_LEGACY = {
        "hardware_gather": "hardware",
        "models_gather": "models_and_agents",
        "efficiency_gather": "efficiency_research",
        "community_gather": "deals_and_blogs",
    }
    
    legacy_checks = {}
    
    for gather_cat, result in gather_results.items():
        parsed = result.get("parsed")
        if not parsed:
            continue
            
        legacy_cat = GATHER_TO_LEGACY.get(gather_cat)
        
        if legacy_cat and _validate_gather_response(parsed):
            # Convert findings list to legacy dict format
            legacy_dict = {}
            findings = parsed.get("findings", [])
            
            if legacy_cat == "hardware":
                # Group findings into hardware check format
                for i, f in enumerate(findings):
                    claim = f.get("claim", "")
                    key = f"finding_{i}"
                    # Try to match known hardware keys
                    claim_lower = claim.lower()
                    if "mac studio" in claim_lower and "m5" in claim_lower:
                        key = "mac_studio_m5"
                    elif "mac studio" in claim_lower and "india" in claim_lower:
                        key = "mac_studio_128gb_india"
                    elif "mac studio" in claim_lower:
                        key = "mac_studio_128gb_us"
                    elif "mac mini" in claim_lower and "india" in claim_lower:
                        key = "mac_mini_48gb_india"
                    elif "mac mini" in claim_lower:
                        key = "mac_mini_48gb_us"
                    elif "framework" in claim_lower:
                        key = "framework_desktop_128gb"
                    elif "strix halo" in claim_lower or "beelink" in claim_lower or "bosgame" in claim_lower:
                        key = "strix_halo_options"
                    elif "rtx 5090" in claim_lower:
                        key = "rtx_5090_india"
                    elif "wwdc" in claim_lower or "apple event" in claim_lower:
                        key = "wwdc_apple_event"
                    
                    legacy_dict[key] = {
                        "info": claim,
                        "in_stock": "available" in claim_lower or "orderable" in claim_lower or "in stock" in claim_lower,
                        "source": f.get("source", ""),
                        "confidence": f.get("confidence_signal", ""),
                    }
                    if f.get("price"):
                        legacy_dict[key]["price"] = f["price"]
            else:
                # Generic conversion for models, efficiency, community
                for i, f in enumerate(findings):
                    key = f"finding_{i}"
                    legacy_dict[key] = {
                        "info": f.get("claim", ""),
                        "found": True,
                        "source": f.get("source", ""),
                        "confidence": f.get("confidence_signal", ""),
                    }
            
            legacy_checks[legacy_cat] = legacy_dict
        elif not legacy_cat:
            # Non-gather results (learning, benchmarks) pass through directly
            legacy_checks[gather_cat] = parsed
    
    return legacy_checks


def _build_changelog_entry(domain: str, change: str, reason: str, evidence: list = None) -> dict:
    """Create a structured changelog entry."""
    return {
        "date": datetime.now().strftime("%Y-%m-%d"),
        "domain": domain,
        "change": change,
        "reason": reason,
        "evidence": evidence or [],
    }


# ─── Phase 3: Researcher Agent ──────────────────────────────────────────────

def _build_researcher_prompt(raw_findings: dict, analytical_state: dict, 
                              pending_questions: list) -> str:
    """Build the researcher prompt with all gathered findings and previous state.
    
    The researcher cross-references findings across domains, resolves conflicts,
    updates the analytical worldview, and answers pending user questions.
    """
    # Serialize analytical state (truncate to avoid token explosion)
    state_summary = {}
    for domain, dstate in analytical_state.items():
        state_summary[domain] = {
            "current_analysis": (dstate.get("current_analysis", "") or "")[:500],
            "confidence": dstate.get("confidence", 0),
            "last_changed": dstate.get("last_changed", ""),
            "num_evidence": len(dstate.get("evidence", [])),
            "options": dstate.get("options", [])[:3],  # top 3 only
        }
    
    # Serialize findings per domain (keep compact)
    findings_text = {}
    for domain, findings in raw_findings.items():
        if findings:
            findings_text[domain] = [
                {"claim": f.get("claim", ""), "source": f.get("source", ""), 
                 "confidence": f.get("confidence_signal", ""), "date": f.get("date", "")}
                for f in findings[:15]  # max 15 per domain
            ]
    
    # Questions
    questions_text = ""
    if pending_questions:
        q_list = [f"Q{i+1}: {q.get('q', '')}" for i, q in enumerate(pending_questions[:5])]
        questions_text = "\n".join(q_list)
    
    prompt = (
        "You are the LLM Homelab Researcher. You maintain a coherent analytical worldview "
        "about local LLM hardware, models, and optimization for coding agents. "
        f"Today is {datetime.now().strftime('%B %d, %Y')}. "
        "\n\nPREVIOUS ANALYTICAL STATE:\n"
        f"{json.dumps(state_summary, indent=None, default=str)}"
        "\n\nNEW FINDINGS FROM TODAY'S GATHER:\n"
        f"{json.dumps(findings_text, indent=None, default=str)}"
    )
    
    if questions_text:
        prompt += f"\n\nPENDING USER QUESTIONS:\n{questions_text}"
    
    prompt += (
        "\n\nYOUR TASK:"
        "\n1. Cross-reference new findings across domains (does a hardware finding affect model choices?)"
        "\n2. Identify conflicts with previous state and RESOLVE them with reasoning"
        "\n3. Update confidence levels based on evidence count and corroboration"
        "\n4. Detect cascading implications (e.g., new GPU price affects cost analysis)"
        "\n5. Answer pending questions using full analytical context"
        "\n6. Generate updated options with trade-off analysis for each domain"
        "\n\nGROUNDING RULES:"
        "\n- Multiple independent benchmarks (3+) = HIGH confidence"
        "\n- Community corroborated (10+ upvotes) = MEDIUM-HIGH"
        "\n- Official specs = BASELINE (real-world typically 85-92% of official)"
        "\n- Single anecdote = LOW (flag as unverified)"
        "\n- When official != real-world: show both + explain gap"
        "\n\nReturn ONLY a JSON object with this structure:"
        '\n{"domain_updates": {'
        '\n  "hardware": {'
        '\n    "analysis": "updated 2-3 paragraph analysis incorporating new findings...",'
        '\n    "options": [{"name": "Option name", "pros": ["..."], "cons": ["..."], '
        '"cost": "INR X / $Y", "confidence": 0.85, "rank": 1, "verdict": "Best for..."}],'
        '\n    "confidence": 0.87,'
        '\n    "new_evidence_incorporated": ["claim 1 from findings", "claim 2"],'
        '\n    "conflicts_resolved": [{"claim_a": "...", "claim_b": "...", "resolution": "...", "reasoning": "..."}],'
        '\n    "changed": true,'
        '\n    "change_reason": "New evidence about X shifts recommendation because..."'
        '\n  },'
        '\n  "models": { ...same structure... },'
        '\n  "optimization": { ...same structure... },'
        '\n  "setup": { ...same structure... }'
        '\n},'
        '\n"cascading_effects": [{"from_domain": "hardware", "to_domain": "models", "implication": "..."}],'
        '\n"deep_dive_requests": [{"topic": "...", "question": "...", "why_needed": "..."}],'
        '\n"question_answers": [{"q_id": 1, "answer": "...", "confidence": 0.8, '
        '"options": [{"choice": "...", "pros": ["..."], "cons": ["..."]}]}],'
        '\n"overall_recommendation": {"action": "buy|wait|build", "target": "product/config", '
        '"reasoning": "...", "confidence": 0.75}'
        '\n}'
        "\n\nIMPORTANT:"
        "\n- For EACH domain, provide 2-5 ranked options with trade-offs"
        "\n- Mark changed=false if no new evidence affects that domain"
        "\n- Confidence should reflect EVIDENCE quality not just quantity"
        "\n- Be specific with numbers (prices, tok/s, VRAM)"
        "\n- Return ONLY the JSON."
    )
    
    return prompt


def _apply_researcher_output(state: dict, researcher_response: dict) -> dict:
    """Apply researcher analysis to update the analytical state.
    
    Updates per-domain analysis, options, confidence, evidence.
    Appends to changelog. Moves answered questions from pending to answered.
    """
    analytical_state = state.get("analytical_state", {})
    changelog = state.get("changelog", [])
    questions = state.get("questions", {"pending": [], "answered": []})
    
    domain_updates = researcher_response.get("domain_updates", {})
    
    for domain, update in domain_updates.items():
        if domain not in analytical_state:
            analytical_state[domain] = {
                "current_analysis": "",
                "options": [],
                "confidence": 0.0,
                "evidence": [],
                "conflicts_resolved": [],
                "last_changed": "",
                "change_reason": "",
            }
        
        ds = analytical_state[domain]
        
        # Update analysis
        if update.get("analysis"):
            ds["current_analysis"] = update["analysis"]
        
        # Update options
        if update.get("options"):
            ds["options"] = update["options"]
        
        # Update confidence
        if "confidence" in update:
            ds["confidence"] = update["confidence"]
        
        # Append new evidence
        new_evidence = update.get("new_evidence_incorporated", [])
        for claim in new_evidence:
            ds["evidence"].append({
                "claim": claim,
                "source": "researcher_analysis",
                "source_type": "cross_referenced",
                "date": datetime.now().strftime("%Y-%m-%d"),
                "confidence_signal": "multiple_reports",
            })
        # Keep evidence capped at 50
        ds["evidence"] = ds["evidence"][-50:]
        
        # Record conflicts resolved
        conflicts = update.get("conflicts_resolved", [])
        if conflicts:
            ds["conflicts_resolved"].extend(conflicts)
            ds["conflicts_resolved"] = ds["conflicts_resolved"][-10:]  # keep last 10
        
        # Track changes
        if update.get("changed", False):
            ds["last_changed"] = datetime.now().strftime("%Y-%m-%d")
            ds["change_reason"] = update.get("change_reason", "Updated by researcher")
            
            # Add changelog entry
            changelog.append(_build_changelog_entry(
                domain=domain,
                change=update.get("change_reason", "Analysis updated"),
                reason=f"New evidence: {', '.join(new_evidence[:3])}",
                evidence=new_evidence[:5],
            ))
    
    # Apply cascading effects to changelog
    cascading = researcher_response.get("cascading_effects", [])
    for effect in cascading:
        changelog.append(_build_changelog_entry(
            domain=effect.get("to_domain", "unknown"),
            change=f"Cascade from {effect.get('from_domain', '?')}: {effect.get('implication', '')}",
            reason="Cross-domain implication detected by researcher",
        ))
    
    # Handle question answers
    q_answers = researcher_response.get("question_answers", [])
    for qa in q_answers:
        q_id = qa.get("q_id")
        if q_id is not None and questions["pending"]:
            # Move from pending to answered (by index, 1-based)
            idx = q_id - 1
            if 0 <= idx < len(questions["pending"]):
                answered_q = questions["pending"].pop(idx)
                answered_q["answer"] = qa.get("answer", "")
                answered_q["answer_confidence"] = qa.get("confidence", 0)
                answered_q["answer_options"] = qa.get("options", [])
                answered_q["answered_on"] = datetime.now().strftime("%Y-%m-%d")
                questions["answered"].append(answered_q)
    
    # Update overall recommendation if provided
    overall_rec = researcher_response.get("overall_recommendation")
    if overall_rec:
        state["recommendation"] = {
            "recommendation": overall_rec.get("action", "wait"),
            "best_option": overall_rec.get("target", ""),
            "reasoning": overall_rec.get("reasoning", ""),
            "confidence": overall_rec.get("confidence", 0),
            "updated_by": "researcher_agent",
            "updated_on": datetime.now().strftime("%Y-%m-%d"),
        }
    
    # Keep changelog capped at 180 entries
    changelog = changelog[-180:]
    
    # Applied mastery tracking: if researcher references topics from knowledge_state,
    # mark them as "applied" (bidirectional learning-research integration)
    knowledge_state = state.get("knowledge_state", {})
    ks_topics = knowledge_state.get("topics", {})
    if ks_topics and domain_updates:
        # Check if any known topic_ids appear in the researcher's analysis text
        all_analysis_text = " ".join(
            u.get("analysis", "") for u in domain_updates.values() if isinstance(u, dict)
        ).lower()
        for topic_id, topic_info in ks_topics.items():
            if topic_info.get("status") in ("introduced", "reinforced") and topic_id in all_analysis_text:
                topic_info["status"] = "applied"
                topic_info["applied_on"] = datetime.now().strftime("%Y-%m-%d")
    
    state["analytical_state"] = analytical_state
    state["changelog"] = changelog
    state["questions"] = questions
    
    return state


def _run_researcher(state: dict, raw_findings: dict) -> dict | None:
    """Execute the researcher agent and return parsed response.
    
    Returns the researcher's JSON response or None if it fails.
    """
    analytical_state = state.get("analytical_state", {})
    pending_questions = state.get("questions", {}).get("pending", [])
    
    prompt = _build_researcher_prompt(raw_findings, analytical_state, pending_questions)
    
    logger.info(f"Running researcher agent ({len(prompt)} chars prompt)...")
    response, parsed = run_copilot_with_retry(prompt, category="researcher", timeout=240)
    
    if parsed and "domain_updates" in parsed:
        logger.info(f"Researcher returned updates for: {list(parsed.get('domain_updates', {}).keys())}")
        return parsed
    elif parsed:
        logger.warning(f"Researcher response missing 'domain_updates' key. Got: {list(parsed.keys())[:5]}")
        # Try to salvage — if it has any domain names at top level
        if any(k in parsed for k in ("hardware", "models", "optimization", "setup")):
            logger.info("Salvaging: wrapping top-level domains as domain_updates")
            return {"domain_updates": parsed}
        return None
    else:
        logger.error("Researcher agent returned no parseable response")
        return None


# ─── Phase 4: Critic Agent ───────────────────────────────────────────────────

def _build_critic_prompt(analytical_state: dict) -> str:
    """Build the critic prompt to review analysis for gaps and contradictions."""
    # Compact state for critic review
    state_for_review = {}
    for domain, ds in analytical_state.items():
        state_for_review[domain] = {
            "analysis": (ds.get("current_analysis", "") or "")[:400],
            "confidence": ds.get("confidence", 0),
            "num_options": len(ds.get("options", [])),
            "num_evidence": len(ds.get("evidence", [])),
            "last_changed": ds.get("last_changed", "never"),
            "conflicts": len(ds.get("conflicts_resolved", [])),
        }
    
    prompt = (
        "You are the LLM Homelab Critic. Your job is quality assurance of the analysis. "
        f"Today is {datetime.now().strftime('%B %d, %Y')}. "
        "\n\nCURRENT ANALYTICAL STATE TO REVIEW:\n"
        f"{json.dumps(state_for_review, indent=None, default=str)}"
        "\n\nCHECK FOR THESE ISSUES:"
        "\n1. UNSUPPORTED CLAIMS: Any domain with confidence > 0.7 but fewer than 3 evidence items"
        "\n2. CROSS-DOMAIN CONTRADICTIONS: Hardware page says X but models page assumes Y"
        "\n3. STALE CLAIMS: Any domain not updated in 7+ days that might have new developments"
        "\n4. MISSING PERSPECTIVES: Only 1 option where 3+ viable alternatives exist"
        "\n5. INCOMPLETE ANALYSIS: Any domain with empty analysis or confidence 0"
        "\n\nReturn ONLY a JSON object:"
        '\n{"status": "clean|gaps_found",'
        '\n "issues": ['
        '\n   {"domain": "hardware", "type": "unsupported_claim|contradiction|stale|missing_options|incomplete",'
        '\n    "severity": "high|medium|low",'
        '\n    "description": "what is wrong",'
        '\n    "suggestion": "what to investigate"}'
        '\n ],'
        '\n "loop_back_questions": ['
        '\n   "Specific question for the researcher to investigate"'
        '\n ],'
        '\n "quality_score": 75'
        '\n}'
        "\n\nBe constructive but strict. Only flag REAL issues that would mislead the user. "
        "\nDo NOT flag stylistic issues or minor omissions. "
        "\nReturn ONLY the JSON."
    )
    return prompt


def _run_critic(state: dict) -> dict | None:
    """Execute the critic agent and return parsed response."""
    analytical_state = state.get("analytical_state", {})
    prompt = _build_critic_prompt(analytical_state)
    
    logger.info(f"Running critic agent ({len(prompt)} chars prompt)...")
    response, parsed = run_copilot_with_retry(prompt, category="critic", timeout=180)
    
    if parsed and "status" in parsed:
        status = parsed.get("status", "unknown")
        issues = parsed.get("issues", [])
        logger.info(f"Critic verdict: {status}, {len(issues)} issues found")
        return parsed
    elif parsed:
        logger.warning(f"Critic response missing 'status'. Got keys: {list(parsed.keys())[:5]}")
        return None
    else:
        logger.error("Critic agent returned no parseable response")
        return None


def _run_critique_loop(state: dict, raw_findings: dict, max_iterations: int = 2) -> dict:
    """Run the critique loop: critic reviews, researcher fixes, repeat.
    
    Max iterations bounds the loop to prevent runaway API costs.
    Returns updated state after all iterations.
    """
    for iteration in range(max_iterations):
        logger.info(f"Critique iteration {iteration + 1}/{max_iterations}")
        
        critic_output = _run_critic(state)
        
        if not critic_output:
            logger.warning("Critic failed, proceeding with current analysis")
            break
        
        status = critic_output.get("status", "clean")
        issues = critic_output.get("issues", [])
        quality_score = critic_output.get("quality_score", 0)
        
        # Store critique metadata
        state.setdefault("pipeline_meta", {})["last_critique"] = {
            "status": status,
            "quality_score": quality_score,
            "issues_count": len(issues),
            "iteration": iteration + 1,
            "timestamp": datetime.now().isoformat(),
        }
        
        if status == "clean" or not issues:
            logger.info(f"Critic says CLEAN (quality: {quality_score}/100). No more iterations needed.")
            break
        
        # Filter only high/medium severity issues
        actionable = [i for i in issues if i.get("severity") in ("high", "medium")]
        if not actionable:
            logger.info(f"Critic found {len(issues)} issues but none high/medium severity. Accepting.")
            break
        
        logger.info(f"Critic found {len(actionable)} actionable issues. Running researcher follow-up...")
        
        # Build follow-up questions from critic
        loop_back_questions = critic_output.get("loop_back_questions", [])
        if not loop_back_questions:
            # Generate from issues
            loop_back_questions = [
                f"[{i['domain']}] {i['suggestion']}" 
                for i in actionable[:3]
            ]
        
        # Run researcher with targeted questions
        follow_up_findings = {
            "critic_questions": [{"claim": q, "source": "critic_agent", 
                                  "confidence_signal": "follow_up_needed", "date": datetime.now().strftime("%Y-%m-%d")} 
                                 for q in loop_back_questions[:3]]
        }
        
        # Merge with existing raw findings for context
        combined_findings = dict(raw_findings)
        combined_findings["critic_followup"] = follow_up_findings["critic_questions"]
        
        researcher_output = _run_researcher(state, combined_findings)
        if researcher_output:
            state = _apply_researcher_output(state, researcher_output)
            logger.info("Researcher follow-up applied successfully")
        else:
            logger.warning("Researcher follow-up failed, keeping current analysis")
            break
    
    return state


def run_pipeline(state: dict) -> dict:
    """Execute the full LLM Homelab analytical pipeline.
    
    Pipeline stages:
      1. GATHER (parallel) — collect raw findings from multiple domains
      2. ANALYZE (sequential) — cross-reference findings with previous state
      3. CRITIQUE (sequential) — validate analysis completeness
      4. PRESENT (sequential) — generate dashboard pages
    """
    today = datetime.now().strftime("%B %d, %Y")
    old_checks = state.get("checks", {})
    
    logger.info("=" * 60)
    logger.info("LLM Homelab Pipeline - Stage 1: GATHER (parallel)")
    logger.info("=" * 60)
    
    # ── STAGE 1: GATHER (parallel) ──────────────────────────────────────────
    gather_results = _run_gather_parallel(state, today)
    
    # Convert v2 gather results to legacy format for backward compatibility
    legacy_checks = _convert_gather_to_legacy(gather_results)
    
    # Process all results into checks
    new_checks = {}
    run_status = {}
    
    # First, apply legacy-converted gather results
    new_checks.update(legacy_checks)
    
    for category, result in gather_results.items():
        parsed = result.get("parsed")
        response = result.get("response", "")
        
        if parsed:
            run_status[category] = "success"
            
            # Non-gather results (learning, benchmarks) go directly into checks
            if category in ("learning_feed", "model_benchmarks"):
                new_checks[category] = parsed
            
            # Update knowledge state from learning results
            if category == "learning_feed":
                ks = state.get("knowledge_state") or _init_knowledge_state()
                ks = _update_knowledge_state(ks, parsed)
                state["knowledge_state"] = ks
                learner_contexts = parsed.get("learner_contexts", {})
                if learner_contexts:
                    state["learner_contexts"] = learner_contexts
        else:
            run_status[category] = "error"
            if response:
                logger.error(f"{category}: failed to parse JSON after retries")
                raw_file = MONITOR_DIR / f"raw_{category}_{datetime.now().strftime('%Y%m%d')}.txt"
                raw_file.write_text(response, encoding="utf-8")
    
    # Fill in missing categories from old checks
    for cat in old_checks:
        if cat not in new_checks:
            new_checks[cat] = old_checks[cat]
    
    # Store raw gather findings in pipeline_meta for future researcher agent
    raw_findings = {}
    for cat, result in gather_results.items():
        parsed = result.get("parsed")
        if parsed and _validate_gather_response(parsed):
            raw_findings[cat] = parsed.get("findings", [])
    
    state.setdefault("pipeline_meta", {})["last_gather_results"] = raw_findings
    state["pipeline_meta"]["last_pipeline_run"] = datetime.now().isoformat()
    state["pipeline_meta"]["gather_status"] = {
        cat: run_status.get(cat, "unknown") for cat in gather_results
    }
    
    # Log gather summary
    total_findings = sum(len(f) for f in raw_findings.values())
    nothing_new_count = sum(
        1 for cat, r in gather_results.items()
        if (r.get("parsed") or {}).get("nothing_new", False)
    )
    logger.info(f"Gather complete: {total_findings} findings across {len(raw_findings)} domains, "
                f"{nothing_new_count} domains unchanged")
    
    # ── STAGE 2: ANALYZE (researcher agent) ─────────────────────────────────
    if raw_findings and total_findings > 0:
        logger.info("=" * 60)
        logger.info("LLM Homelab Pipeline - Stage 2: ANALYZE (researcher)")
        logger.info("=" * 60)
        
        researcher_output = _run_researcher(state, raw_findings)
        
        if researcher_output:
            state = _apply_researcher_output(state, researcher_output)
            run_status["researcher"] = "success"
            
            # Count what changed
            changed_domains = [
                d for d, u in researcher_output.get("domain_updates", {}).items()
                if u.get("changed", False)
            ]
            logger.info(f"Researcher updated {len(changed_domains)} domains: {changed_domains}")
        else:
            run_status["researcher"] = "error"
            logger.warning("Researcher failed — legacy enrichment will handle analysis")
    else:
        logger.info("Stage 2: SKIP (no new findings to analyze)")
        run_status["researcher"] = "skipped"
    
    # ── STAGE 3: CRITIQUE (bounded loop) ──
    if run_status.get("researcher") == "success":
        logger.info("=" * 60)
        logger.info("LLM Homelab Pipeline - Stage 3: CRITIQUE (bounded loop)")
        logger.info("=" * 60)
        
        state = _run_critique_loop(state, raw_findings, max_iterations=2)
        run_status["critique"] = state.get("pipeline_meta", {}).get("last_critique", {}).get("status", "unknown")
        logger.info(f"Critique complete: {run_status['critique']}")
    else:
        logger.info("Stage 3: SKIP (researcher did not run or failed)")
        run_status["critique"] = "skipped"
    
    # Return processed results for the rest of main() to use
    return {
        "new_checks": new_checks,
        "run_status": run_status,
        "gather_results": gather_results,
        "raw_findings": raw_findings,
    }


def run_copilot(prompt: str, timeout: int = 180, category: str = "general") -> str:
    """Run a Copilot CLI prompt and return the raw response text.
    
    Calls node directly with the npm-loader.js script, bypassing the .cmd
    wrapper to avoid cmd.exe metacharacter interpretation issues on Windows.
    Creates a named session per category so it can be resumed later via
    'copilot --resume="llm-homelab-{category}-{date}-{hex}"'.
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
    [bullet] Web Search... [bullet] Web Search... {"key": "value", ...}
    
    Strategy: find the last JSON object in the text (the actual response),
    ignoring intermediate JSON fragments from tool outputs.
    """
    if not text:
        return None

    # Strip markdown code fences (including closing ```)
    cleaned = re.sub(r"```(?:json)?\s*", "", text)
    cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE)
    # Strip ANSI escape codes
    cleaned = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', cleaned)
    # Strip tool progress lines (● Web Search..., * Searching...)
    cleaned = re.sub(r'^[●\u2022\*].*$', '', cleaned, flags=re.MULTILINE)
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

    # Strategy 2: Strip progress/tool lines and try again
    lines = cleaned.split('\n')
    json_lines = [l for l in lines if not l.strip().startswith(('\u25cf', '\u2022', '*')) 
                  and 'Web Search' not in l and 'Searching' not in l]
    cleaned2 = '\n'.join(json_lines).strip()
    if cleaned2 != cleaned:
        for match in re.finditer(r'\{"[a-zA-Z_]', cleaned2):
            pos = match.start()
            candidate = cleaned2[pos:]
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
                        return parsed
                except json.JSONDecodeError:
                    continue

    # Strategy 3: Try direct parse of the whole text (unlikely but cheap)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    logger.warning(f"Could not parse JSON from response ({len(text)} chars)")
    # Dump failed response to temp file for debugging
    debug_file = MONITOR_DIR / f"_debug_parse_fail_{datetime.now().strftime('%H%M%S')}.txt"
    try:
        debug_file.write_text(text[:2000], encoding="utf-8")
        logger.debug(f"  Dumped to {debug_file}")
    except Exception:
        pass
    return None


# Expected keys per category — used for schema validation on parse
EXPECTED_KEYS = {
    "hardware": {"mac_studio_m5", "mac_studio_128gb_india", "mac_studio_128gb_us"},
    "models_and_agents": {"best_local_coding_models", "inference_runtimes"},
    "deals_and_blogs": {"apple_india_deals", "latest_local_llm_news"},
    "efficiency_research": {"quantization_breakthroughs", "inference_engine_updates", "moe_offloading"},
    "learning_feed": {"articles", "title"},
    "model_benchmarks": {"top_coding_models", "name"},
    # v2 gather format (Phase 2) — validated separately by _validate_gather_response
    "hardware_gather": {"findings", "nothing_new"},
    "models_gather": {"findings", "nothing_new"},
    "efficiency_gather": {"findings", "nothing_new"},
    "community_gather": {"findings", "nothing_new"},
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
    full_title = f"{icon} LLM Homelab"

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
        "  LLM Homelab — DAILY SUMMARY",
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

  /* ── Analysis Pages ── */
  .analysis-position { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 24px; margin-bottom: 24px; }
  .analysis-position p { font-size: 1.05em; line-height: 1.8; color: var(--text); }
  .confidence-bar-wrap { display: flex; align-items: center; gap: 16px; margin-bottom: 24px; padding: 16px; background: var(--card); border-radius: var(--radius); }
  .confidence-ring { width: 64px; height: 64px; flex-shrink: 0; }
  .confidence-info { flex: 1; }
  .confidence-info .label { font-size: 0.85em; color: var(--dim); }
  .confidence-info .value { font-size: 1.4em; font-weight: 700; }
  .confidence-info .meta { font-size: 0.8em; color: var(--dim); margin-top: 4px; }
  .discuss-cli-btn { display: inline-flex; align-items: center; gap: 6px; padding: 8px 16px; border-radius: 8px; background: rgba(88,166,255,0.12); color: var(--accent); border: 1px solid rgba(88,166,255,0.3); cursor: pointer; font-size: 0.85em; font-weight: 500; transition: all 0.2s; }
  .discuss-cli-btn:hover { background: rgba(88,166,255,0.2); border-color: var(--accent); }
  .options-matrix { width: 100%; border-collapse: collapse; margin-bottom: 24px; font-size: 0.88em; }
  .options-matrix th { text-align: left; padding: 10px 14px; background: var(--card); border-bottom: 1px solid var(--border); font-size: 0.78em; text-transform: uppercase; letter-spacing: 0.5px; color: var(--dim); font-weight: 600; }
  .options-matrix td { padding: 10px 14px; border-bottom: 1px solid var(--border); vertical-align: top; }
  .options-matrix tr:hover td { background: rgba(88,166,255,0.04); }
  .options-matrix .rank-1 td { border-left: 3px solid var(--green); }
  .options-matrix .rank-2 td { border-left: 3px solid var(--accent); }
  .options-matrix .pros { color: var(--green); font-size: 0.85em; }
  .options-matrix .cons { color: var(--red); font-size: 0.85em; }
  .evidence-list { list-style: none; padding: 0; margin-bottom: 24px; }
  .evidence-list li { padding: 10px 14px; background: var(--card); border-radius: 8px; margin-bottom: 8px; font-size: 0.88em; display: flex; align-items: flex-start; gap: 10px; border: 1px solid var(--border); }
  .evidence-list .ev-icon { font-size: 1.1em; flex-shrink: 0; }
  .evidence-list .ev-text { flex: 1; line-height: 1.5; }
  .conflicts-list { margin-bottom: 24px; }
  .conflicts-list .conflict-item { padding: 12px 16px; background: var(--card); border-radius: 8px; margin-bottom: 8px; border-left: 3px solid var(--yellow); }
  .domain-changelog { margin-bottom: 24px; }
  .domain-changelog .entry { padding: 10px 14px; border-left: 2px solid var(--accent2); margin-bottom: 8px; margin-left: 8px; }
  .domain-changelog .entry-date { font-size: 0.78em; color: var(--dim); }
  .domain-changelog .entry-text { font-size: 0.9em; margin-top: 4px; }

  /* ── Situation Room ── */
  .sr-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap: 16px; margin-bottom: 24px; }
  .sr-card { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; text-align: center; transition: all 0.2s; }
  .sr-card:hover { border-color: var(--accent); transform: translateY(-2px); }
  .sr-card .domain-label { font-size: 0.82em; color: var(--dim); text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 8px; }
  .sr-card .top-option { font-size: 1em; font-weight: 600; margin-top: 8px; }
  .sr-card .ev-count { font-size: 0.78em; color: var(--dim); margin-top: 4px; }
  .sr-card .last-updated { font-size: 0.72em; color: var(--dim); margin-top: 4px; }
  .sr-changes { background: var(--surface); border: 1px solid var(--border); border-radius: var(--radius); padding: 20px; margin-bottom: 24px; }
  .sr-pipeline { background: var(--card); border: 1px solid var(--border); border-radius: var(--radius); padding: 16px; margin-bottom: 24px; font-size: 0.88em; }

  /* ── Knowledge Graph (styles defined inline per page) ── */
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
        ("summary", "🏠 Situation Room", "index.html"),
        ("hardware", "🖥️ Hardware", "hardware.html"),
        ("models", "🧠 Models", "models.html"),
        ("optimization", "🔬 Optimization", "optimization.html"),
        ("setup", "⚙️ Setup", "setup.html"),
        ("knowledge", "🗺️ Knowledge", "knowledge.html"),
        ("ask", "❓ Ask", "ask.html"),
        ("timeline", "📈 Timeline", "timeline.html"),
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
        f'<title>LLM Homelab - {title}</title>\n'
        f'<style>{_DASHBOARD_CSS}</style>\n'
        f'</head>\n<body data-run-id="{_RUN_ID}" data-run-date="{_RUN_DATE}">\n'
        + nav_html + '\n'
        + body_content + '\n'
        + _MODAL_OVERLAY_HTML + '\n'
        + f'<div class="footer">\n'
        f'  <strong>LLM Homelab</strong> &middot; '
        f'  Powered by <a href="https://github.com/features/copilot">GitHub Copilot CLI</a> &middot;\n'
        f'  Auto-updates daily &middot;\n'
        f'  <a href="file:///{monitor_dir_uri}/monitor.log">Log</a> &middot;\n'
        f'  <a href="file:///{monitor_dir_uri}/monitor_state.json">State</a>\n'
        f'</div>\n'
        f'<script>{js}\n</script>\n'
        '</body>\n</html>'
    )


def _legacy_generate_main_page(state, checks, enrichment, cat_icons, cat_labels, link_map, modal_data, now, run_status, timeline):
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

        detail_text = rec_summary_text or rec_best or rec.get("desc", "") or ""
        if rec_wait and not detail_text:
            detail_text = rec_wait
        if not detail_text:
            detail_text = f"Monitoring {rec.get('title', 'options')} — run the monitor for a full analysis"

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
        '\n    <h1>\U0001F5A5\uFE0F LLM Homelab</h1>'
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
    return _generate_page_shell("LLM Homelab", nav_html, body_content, modal_json)


def _legacy_generate_hardware_page(checks, enrichment, cat_icons, cat_labels, modal_data, now):
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
        '\n    <h1>\U0001F5A5\uFE0F LLM Homelab</h1>'
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
    return _generate_page_shell("Hardware - LLM Homelab", nav_html, body_content, modal_json)


def _legacy_generate_models_page(checks, enrichment, cat_icons, cat_labels, modal_data, now):
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
        '\n    <h1>\U0001F5A5\uFE0F LLM Homelab</h1>'
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
    return _generate_page_shell("Models & Agents - LLM Homelab", nav_html, body_content, modal_json)


def _legacy_generate_efficiency_page(checks, enrichment, modal_data, now):
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
        '\n    <h1>\U0001F5A5\uFE0F LLM Homelab</h1>'
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
    return _generate_page_shell("Efficiency Research - LLM Homelab", nav_html, body_content, modal_json)


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
        '\n    <h1>\U0001F5A5\uFE0F LLM Homelab</h1>'
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
    return _generate_page_shell("Deals & News - LLM Homelab", nav_html, body_content, modal_json)


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


def _generate_dag_visualization(ks_topics: dict) -> str:
    """Generate an interactive SVG DAG visualization of the knowledge map."""
    # Short labels for each topic
    SHORT_LABELS = {
        "tokens_and_parameters": "Tokens & Params",
        "vram_calculation": "VRAM Calc",
        "context_window_math": "Context Windows",
        "prefill_vs_decode": "Prefill/Decode",
        "kv_cache_growth": "KV Cache",
        "memory_bandwidth_vs_compute": "Mem Bandwidth",
        "latency_vs_throughput": "Latency/Throughput",
        "quantization_basics": "Quantization",
        "gguf_formats": "GGUF Formats",
        "exl2_awq_gptq": "EXL2/AWQ/GPTQ",
        "offloading_gpu_cpu_disk": "Offloading",
        "moe_expert_routing": "MoE Routing",
        "speculative_decoding": "Spec Decoding",
        "llama_cpp": "llama.cpp",
        "ktransformers": "KTransformers",
        "vllm_tensorrt": "vLLM/TensorRT",
        "mlx_apple_silicon": "MLX (Apple)",
        "runtime_compat": "Runtime Compat",
        "dense_vs_moe": "Dense vs MoE",
        "coding_model_traits": "Coding Models",
        "reasoning_chains": "Reasoning",
        "model_selection_for_agents": "Model Selection",
        "agent_context_management": "Agent Context",
        "yolo_coding_mode": "YOLO Mode",
        "batch_concurrency": "Batch/Concurrency",
        "tool_use_function_calling": "Tool Use",
        "vram_tiers_and_gpus": "GPU VRAM Tiers",
        "ram_bandwidth_for_offload": "RAM Bandwidth",
        "pcie_lanes_multi_gpu": "PCIe/Multi-GPU",
        "ssd_weight_loading": "SSD Loading",
        "power_thermals_noise": "Power/Thermals",
        "os_runtime_friction": "OS/Runtime",
        "lora_qlora_basics": "LoRA/QLoRA",
        "when_to_finetune": "When to Fine-tune",
    }

    STATUS_COLORS = {
        "unseen": "#555555",
        "introduced": "#f59e0b",
        "reinforced": "#10b981",
        "applied": "#6366f1",
    }
    STATUS_GLOW = {
        "unseen": "none",
        "introduced": "0 0 8px #f59e0b55",
        "reinforced": "0 0 8px #10b98155",
        "applied": "0 0 10px #6366f155",
    }
    ARROW_COLORS = {
        "unseen": "#444444",
        "introduced": "#f59e0b88",
        "reinforced": "#10b98188",
        "applied": "#6366f188",
    }

    layer_names = {
        0: "Foundations", 1: "Core Inference", 2: "Optimization",
        3: "Engines", 4: "Models", 5: "Agents",
        6: "Hardware", 7: "Fine-tune"
    }

    # Layout parameters
    node_w, node_h = 120, 40
    h_gap, v_gap = 30, 100
    top_margin, left_margin = 60, 90
    num_layers = 8

    # Group topics by layer
    layers = {i: [] for i in range(num_layers)}
    for tid, tdata in TOPIC_DAG.items():
        layers[tdata["layer"]].append(tid)

    # Calculate max width needed
    max_nodes_in_layer = max(len(v) for v in layers.values()) if layers else 1
    svg_width = max(1000, left_margin + max_nodes_in_layer * (node_w + h_gap))
    svg_height = top_margin + num_layers * (node_h + v_gap) + 40

    # Position each node
    node_positions = {}  # tid -> (cx, cy)
    for layer_num in range(num_layers):
        tids = layers[layer_num]
        n = len(tids)
        if n == 0:
            continue
        total_width = n * node_w + (n - 1) * h_gap
        start_x = (svg_width - left_margin) / 2 - total_width / 2 + left_margin
        y = top_margin + layer_num * (node_h + v_gap)
        for i, tid in enumerate(tids):
            cx = start_x + i * (node_w + h_gap) + node_w / 2
            cy = y + node_h / 2
            node_positions[tid] = (cx, cy)

    # Build SVG elements
    arrows_svg = ""
    nodes_svg = ""

    # Draw edges (arrows from prereq to dependent)
    for tid, tdata in TOPIC_DAG.items():
        if tid not in node_positions:
            continue
        target_status = ks_topics.get(tid, {}).get("status", "unseen")
        tx, ty = node_positions[tid]
        target_top = (tx, ty - node_h / 2)

        for prereq in tdata.get("prereqs", []):
            if prereq not in node_positions:
                continue
            px, py = node_positions[prereq]
            source_bottom = (px, py + node_h / 2)

            # Cubic bezier: go down from source, up into target
            sx, sy = source_bottom
            ex, ey = target_top
            mid_y = (sy + ey) / 2
            # Control points for smooth curve
            c1x, c1y = sx, sy + (mid_y - sy) * 0.7
            c2x, c2y = ex, ey - (ey - mid_y) * 0.7

            prereq_status = ks_topics.get(prereq, {}).get("status", "unseen")
            arrow_color = ARROW_COLORS.get(target_status, "#444")
            dash = 'stroke-dasharray="4 3"' if prereq_status == "unseen" else ""
            opacity = "0.4" if target_status == "unseen" else "0.7"

            arrows_svg += (
                f'<path d="M{sx:.1f},{sy:.1f} C{c1x:.1f},{c1y:.1f} {c2x:.1f},{c2y:.1f} {ex:.1f},{ey:.1f}" '
                f'fill="none" stroke="{arrow_color}" stroke-width="1.5" {dash} opacity="{opacity}" '
                f'marker-end="url(#arrow-{target_status})"/>\n'
            )

    # Draw nodes
    for tid in TOPIC_DAG:
        if tid not in node_positions:
            continue
        cx, cy = node_positions[tid]
        status = ks_topics.get(tid, {}).get("status", "unseen")
        color = STATUS_COLORS.get(status, "#555")
        glow = STATUS_GLOW.get(status, "none")
        label = _esc(SHORT_LABELS.get(tid, tid[:14]))
        full_title = _esc(TOPIC_DAG[tid].get("title", tid))
        confidence = ks_topics.get(tid, {}).get("confidence", 0)
        conf_str = f" ({int(confidence*100)}%)" if status != "unseen" else ""

        rx, ry = cx - node_w / 2, cy - node_h / 2
        fill_opacity = "0.15" if status == "unseen" else "0.25"
        stroke_w = "1" if status == "unseen" else "2"
        font_size = "10" if len(label) > 14 else "11"

        filter_attr = f'filter="url(#glow-{status})"' if status != "unseen" else ""
        nodes_svg += (
            f'<g class="dag-node" data-topic="{tid}">'
            f'<title>{full_title}{conf_str}</title>'
            f'<rect x="{rx:.1f}" y="{ry:.1f}" width="{node_w}" height="{node_h}" '
            f'rx="8" ry="8" fill="{color}" fill-opacity="{fill_opacity}" '
            f'stroke="{color}" stroke-width="{stroke_w}" {filter_attr}/>'
            f'<text x="{cx:.1f}" y="{cy + 4:.1f}" text-anchor="middle" '
            f'font-size="{font_size}" fill="{color}" font-family="system-ui, sans-serif" '
            f'font-weight="500">{label}</text>'
            f'</g>\n'
        )

    # Layer labels on left
    layer_labels_svg = ""
    for layer_num in range(num_layers):
        if not layers[layer_num]:
            continue
        y = top_margin + layer_num * (node_h + v_gap) + node_h / 2
        name = layer_names.get(layer_num, f"L{layer_num}")
        layer_labels_svg += (
            f'<text x="10" y="{y + 4:.1f}" font-size="10" fill="#888" '
            f'font-family="system-ui, sans-serif" font-weight="600">'
            f'L{layer_num}</text>\n'
            f'<text x="10" y="{y + 18:.1f}" font-size="9" fill="#666" '
            f'font-family="system-ui, sans-serif">{_esc(name)}</text>\n'
        )

    # Legend
    legend_x = svg_width - 200
    legend_svg = (
        f'<g transform="translate({legend_x}, 10)">'
        f'<text x="0" y="12" font-size="10" fill="#888" font-family="system-ui, sans-serif" font-weight="600">Status</text>'
    )
    for i, (status, color) in enumerate(STATUS_COLORS.items()):
        ly = 26 + i * 18
        legend_svg += (
            f'<rect x="0" y="{ly - 8}" width="12" height="12" rx="3" '
            f'fill="{color}" fill-opacity="0.3" stroke="{color}" stroke-width="1.5"/>'
            f'<text x="18" y="{ly + 2}" font-size="9" fill="{color}" '
            f'font-family="system-ui, sans-serif">{status.capitalize()}</text>'
        )
    legend_svg += '</g>'

    # Assemble SVG
    svg = f'''<div class="dag-viz-container">
<svg viewBox="0 0 {svg_width} {svg_height}" xmlns="http://www.w3.org/2000/svg"
     class="dag-svg" preserveAspectRatio="xMidYMin meet">
  <defs>
    <marker id="arrow-unseen" viewBox="0 0 10 10" refX="9" refY="5"
      markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#444"/>
    </marker>
    <marker id="arrow-introduced" viewBox="0 0 10 10" refX="9" refY="5"
      markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#f59e0b88"/>
    </marker>
    <marker id="arrow-reinforced" viewBox="0 0 10 10" refX="9" refY="5"
      markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#10b98188"/>
    </marker>
    <marker id="arrow-applied" viewBox="0 0 10 10" refX="9" refY="5"
      markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#6366f188"/>
    </marker>
    <filter id="glow-introduced" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="3" result="blur"/>
      <feFlood flood-color="#f59e0b" flood-opacity="0.3"/>
      <feComposite in2="blur" operator="in"/>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="glow-reinforced" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="3" result="blur"/>
      <feFlood flood-color="#10b981" flood-opacity="0.3"/>
      <feComposite in2="blur" operator="in"/>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
    <filter id="glow-applied" x="-20%" y="-20%" width="140%" height="140%">
      <feGaussianBlur stdDeviation="3" result="blur"/>
      <feFlood flood-color="#6366f1" flood-opacity="0.3"/>
      <feComposite in2="blur" operator="in"/>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  {layer_labels_svg}
  {arrows_svg}
  {nodes_svg}
  {legend_svg}
</svg>
</div>
<div id="dag-detail-panel" class="dag-side-pane"></div>
<div id="dag-pane-overlay" class="dag-pane-overlay"></div>
<style>
.dag-viz-container {{
  overflow-x: auto;
  overflow-y: hidden;
  border-radius: 12px;
  background: rgba(20, 20, 35, 0.5);
  border: 1px solid rgba(255,255,255,0.06);
  padding: 8px;
}}
.dag-svg {{
  width: 100%;
  min-width: 700px;
  height: auto;
  display: block;
}}
.dag-node rect {{
  cursor: pointer;
  transition: fill-opacity 0.2s, stroke-width 0.2s;
}}
.dag-node:hover rect {{
  fill-opacity: 0.4 !important;
  stroke-width: 2.5 !important;
}}
.dag-node:hover text {{
  font-weight: 700 !important;
}}
.dag-side-pane {{
  position: fixed;
  top: 0;
  right: -420px;
  width: 400px;
  height: 100vh;
  padding: 20px 24px;
  background: rgba(18, 18, 30, 0.98);
  border-left: 1px solid var(--accent, #6366f1);
  overflow-y: auto;
  z-index: 1000;
  transition: right 0.3s ease;
  box-shadow: -4px 0 20px rgba(0,0,0,0.4);
}}
.dag-side-pane.open {{
  right: 0;
}}
.dag-pane-overlay {{
  display: none;
  position: fixed;
  top: 0;
  left: 0;
  width: 100vw;
  height: 100vh;
  background: rgba(0,0,0,0.4);
  z-index: 999;
}}
.dag-pane-overlay.open {{
  display: block;
}}
.dag-side-pane .pane-close {{
  position: sticky;
  top: 0;
  float: right;
  cursor: pointer;
  font-size: 1.4rem;
  color: var(--dim, #888);
  background: rgba(18,18,30,0.9);
  border: none;
  padding: 2px 8px;
  border-radius: 4px;
  z-index: 10;
}}
.dag-side-pane .pane-close:hover {{ color: var(--fg, #eee); background: rgba(99,102,241,0.2); }}
@keyframes fadeIn {{ from {{ opacity: 0; transform: translateX(8px); }} to {{ opacity: 1; transform: translateX(0); }} }}
.dag-detail-title {{ font-size: 1.05rem; font-weight: 600; margin-bottom: 8px; }}
.dag-detail-meta {{ display: flex; gap: 8px; flex-wrap: wrap; font-size: 0.8rem; color: var(--dim, #888); margin-bottom: 10px; }}
.dag-detail-meta span {{ padding: 2px 8px; border-radius: 4px; background: rgba(255,255,255,0.05); }}
.dag-detail-facts {{ margin: 8px 0; }}
.dag-detail-facts li {{ font-size: 0.85rem; margin-bottom: 4px; color: #10b981; }}
.dag-detail-prereqs {{ font-size: 0.8rem; color: var(--dim, #888); margin-top: 8px; }}
.dag-detail-prereqs a {{ color: var(--accent, #6366f1); cursor: pointer; text-decoration: underline; }}
.dag-lesson {{ background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; padding: 14px; margin-top: 12px; }}
.dag-lesson-date {{ font-size: 0.75rem; color: var(--dim, #666); margin-bottom: 6px; }}
.dag-lesson-content {{ font-size: 0.85rem; line-height: 1.6; color: var(--fg, #eee); white-space: pre-wrap; max-height: 200px; overflow-y: auto; margin-bottom: 10px; }}
.dag-lesson-exercise {{ background: rgba(245,158,11,0.08); padding: 10px; border-radius: 6px; font-size: 0.85rem; margin-bottom: 10px; }}
.dag-lesson-exercise summary {{ cursor: pointer; color: var(--accent, #6366f1); }}
.dag-lesson-hw {{ font-size: 0.83rem; color: #10b981; padding: 6px 10px; background: rgba(16,185,129,0.08); border-radius: 6px; margin-bottom: 10px; }}
.dag-resources {{ margin-top: 10px; }}
.dag-resources a {{ display: inline-block; font-size: 0.8rem; padding: 3px 10px; margin: 3px 4px 3px 0; border-radius: 4px; background: rgba(99,102,241,0.1); border: 1px solid rgba(99,102,241,0.3); color: var(--accent, #6366f1); text-decoration: none; }}
.dag-resources a:hover {{ background: rgba(99,102,241,0.2); }}
.dag-actions {{ display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }}
.dag-action-btn {{ padding: 6px 14px; border-radius: 6px; font-size: 0.8rem; cursor: pointer; border: 1px solid var(--border, #333); background: var(--bg, #1e1e2e); color: var(--fg, #eee); }}
.dag-action-btn:hover {{ border-color: var(--accent, #6366f1); }}
.dag-action-btn.primary {{ background: var(--accent, #6366f1); color: #000; border-color: var(--accent, #6366f1); }}
.dag-lesson-tabs {{ display: flex; gap: 4px; margin-top: 8px; margin-bottom: 8px; }}
.dag-lesson-tab {{ padding: 4px 10px; border-radius: 4px; font-size: 0.75rem; cursor: pointer; background: rgba(255,255,255,0.05); color: var(--dim, #888); border: 1px solid transparent; }}
.dag-lesson-tab.active {{ border-color: var(--accent, #6366f1); color: var(--accent, #6366f1); }}
@media (max-width: 600px) {{
  .dag-side-pane {{ width: 100vw; right: -100vw; }}
  .dag-side-pane.open {{ right: 0; }}
}}
</style>'''

    # Build topic data JSON for JS interactivity (includes lessons + resources)
    topic_data = {}
    for tid, tinfo in TOPIC_DAG.items():
        ts = ks_topics.get(tid, {})
        topic_data[tid] = {
            "title": tinfo["title"],
            "layer": tinfo["layer"],
            "status": ts.get("status", "unseen"),
            "confidence": ts.get("confidence", 0),
            "key_facts": ts.get("key_facts", [])[:5],
            "last_taught": ts.get("last_taught"),
            "prereqs": tinfo.get("prereqs", []),
            "prereq_titles": [TOPIC_DAG.get(p, {}).get("title", p) for p in tinfo.get("prereqs", [])],
            "goal_tags": tinfo.get("goal_tags", []),
            "lessons": ts.get("lessons", []),
        }
    
    topic_json = json.dumps(topic_data, ensure_ascii=False)
    layer_names_json = json.dumps(layer_names, ensure_ascii=False)
    
    # Get session name for discuss-in-CLI
    sessions = {}
    copilot_sessions = ks_topics  # We'll get from the state passed to the outer function
    # session name will be injected from state in _generate_learning_page
    
    # Add JS (panel div is already in the layout above)
    svg += f'''
<div id="dag-copy-toast" class="copy-toast">Copied to clipboard!</div>
<script>
(function() {{
  var topics = {topic_json};
  var layerNames = {layer_names_json};
  var statusLabels = {{"unseen":"Not yet covered","introduced":"Introduced","reinforced":"Reinforced","applied":"Mastered"}};
  var statusIcons = {{"unseen":"○","introduced":"◐","reinforced":"●","applied":"✓"}};
  var typeIcons = {{"article":"📄","video":"🎬","docs":"📚","tool":"🔧","paper":"📑","repo":"📦"}};
  var panel = document.getElementById("dag-detail-panel");
  var overlay = document.getElementById("dag-pane-overlay");
  
  function escHtml(s) {{ var d=document.createElement("div"); d.textContent=s; return d.innerHTML; }}
  
  function showToast(msg) {{
    var toast = document.getElementById("dag-copy-toast");
    if (toast) {{ toast.textContent = msg; toast.classList.add("show"); setTimeout(function(){{ toast.classList.remove("show"); }}, 2000); }}
  }}
  
  function copyDiscussCmd(topicTitle) {{
    var cmd = 'copilot --resume="llm-monitor-learning" -p "Let\\'s discuss deeper: ' + topicTitle.replace(/"/g, '\\\\"') + '"';
    navigator.clipboard.writeText(cmd).then(function() {{ showToast("CLI command copied! Paste in terminal to discuss."); }});
  }}
  window.copyDiscussCmd = copyDiscussCmd;
  
  document.querySelectorAll(".dag-node").forEach(function(node) {{
    node.style.cursor = "pointer";
    node.addEventListener("click", function(e) {{
      var tid = this.getAttribute("data-topic");
      var t = topics[tid];
      if (!t) return;
      
      // Header
      var html = "<div class=\\"dag-detail-title\\">" + statusIcons[t.status] + " " + escHtml(t.title) + "</div>";
      html += "<div class=\\"dag-detail-meta\\">";
      html += "<span>Layer " + t.layer + ": " + (layerNames[t.layer] || "") + "</span>";
      html += "<span>Status: " + statusLabels[t.status] + "</span>";
      var confPct = t.status !== "unseen" ? Math.round(t.confidence * 100) + "%" : "—";
      html += "<span>Confidence: " + confPct + "</span>";
      if (t.last_taught) html += "<span>Last: " + t.last_taught + "</span>";
      if (t.goal_tags.length) html += "<span>🎯 " + t.goal_tags.join(", ").replace(/-/g, " ") + "</span>";
      html += "</div>";
      
      // Prerequisites
      if (t.prereqs && t.prereqs.length > 0) {{
        html += "<div class=\\"dag-detail-prereqs\\">Prerequisites: " +
          t.prereqs.map(function(p, i) {{
            var pStatus = topics[p] ? topics[p].status : "unseen";
            var icon = statusIcons[pStatus] || "○";
            return "<a onclick=\\"document.querySelector(\\'.dag-node[data-topic=\\\\\\"" + p + "\\\\\\"]\\').dispatchEvent(new Event(\\'click\\'));\\">" + icon + " " + escHtml(t.prereq_titles[i]) + "</a>";
          }}).join(" → ") + "</div>";
      }}
      
      // Lessons (the main content)
      if (t.lessons && t.lessons.length > 0) {{
        // Tabs if multiple lessons
        if (t.lessons.length > 1) {{
          html += "<div class=\\"dag-lesson-tabs\\">";
          t.lessons.forEach(function(l, idx) {{
            var active = idx === t.lessons.length - 1 ? " active" : "";
            html += "<span class=\\"dag-lesson-tab" + active + "\\" onclick=\\"showLessonTab('" + tid + "'," + idx + ")\\">" + (l.date || "Lesson " + (idx+1)) + "</span>";
          }});
          html += "</div>";
        }}
        
        t.lessons.forEach(function(lesson, idx) {{
          var vis = idx === t.lessons.length - 1 ? "" : "display:none;";
          html += "<div class=\\"dag-lesson\\" data-lesson-idx=\\"" + idx + "\\" data-topic-id=\\"" + tid + "\\" style=\\"" + vis + "\\">";
          if (lesson.date) html += "<div class=\\"dag-lesson-date\\">📅 " + lesson.date + "</div>";
          if (lesson.prerequisite_recap) html += "<div style=\\"font-size:0.8rem;color:var(--dim);margin-bottom:8px;font-style:italic\\">🔗 " + escHtml(lesson.prerequisite_recap) + "</div>";
          if (lesson.content) html += "<div class=\\"dag-lesson-content\\">" + escHtml(lesson.content) + "</div>";
          
          // Key takeaways
          if (lesson.key_takeaways && lesson.key_takeaways.length) {{
            html += "<div class=\\"dag-detail-facts\\"><strong>Key Takeaways:</strong><ul>" +
              lesson.key_takeaways.map(function(f) {{ return "<li>" + escHtml(f) + "</li>"; }}).join("") + "</ul></div>";
          }}
          
          // Exercise
          if (lesson.practical_exercise) {{
            html += "<div class=\\"dag-lesson-exercise\\"><strong>🧮 Exercise:</strong> " + escHtml(lesson.practical_exercise);
            if (lesson.answer) html += "<details><summary>Show Answer</summary><p style=\\"color:#10b981\\">" + escHtml(lesson.answer) + "</p></details>";
            html += "</div>";
          }}
          
          // Hardware implication
          if (lesson.hardware_implication) {{
            html += "<div class=\\"dag-lesson-hw\\">🖥️ " + escHtml(lesson.hardware_implication) + "</div>";
          }}
          
          // Resources
          if (lesson.resources && lesson.resources.length) {{
            html += "<div class=\\"dag-resources\\"><strong>📚 Deep Dive Resources:</strong><br>";
            lesson.resources.forEach(function(r) {{
              if (r.url && r.title) {{
                var icon = typeIcons[r.type] || "🔗";
                html += "<a href=\\"" + escHtml(r.url) + "\\" target=\\"_blank\\">" + icon + " " + escHtml(r.title) + "</a>";
              }}
            }});
            html += "</div>";
          }}
          
          html += "</div>";
        }});
      }} else if (t.status === "unseen") {{
        html += "<div style=\\"font-size:0.85rem;color:var(--dim);margin-top:12px;padding:12px;background:rgba(255,255,255,0.03);border-radius:8px\\">🔒 This topic hasn't been covered yet. ";
        if (t.prereqs.length === 0) {{
          html += "It has no prerequisites — it will be taught in the next run!";
        }} else {{
          var unmet = t.prereqs.filter(function(p) {{ return !topics[p] || topics[p].status === "unseen"; }});
          if (unmet.length > 0) {{
            html += "Waiting for: " + unmet.map(function(p) {{ return topics[p] ? topics[p].title : p; }}).join(", ");
          }} else {{
            html += "Prerequisites met — it will be taught in the next run!";
          }}
        }}
        html += "</div>";
      }}
      
      // Actions
      html += "<div class=\\"dag-actions\\">";
      html += "<button class=\\"dag-action-btn primary\\" onclick=\\"copyDiscussCmd('" + escHtml(t.title).replace(/'/g, "\\\\'") + "')\\">💬 Discuss in CLI</button>";
      if (t.status !== "unseen") {{
        html += "<button class=\\"dag-action-btn\\" onclick=\\"copyDiscussCmd('Reinforce my understanding of: " + escHtml(t.title).replace(/'/g, "\\\\'") + "')\\">🔄 Reinforce</button>";
        html += "<button class=\\"dag-action-btn\\" onclick=\\"copyDiscussCmd('Give me a practical exercise for: " + escHtml(t.title).replace(/'/g, "\\\\'") + "')\\">🧮 Practice</button>";
      }}
      html += "</div>";
      
      panel.innerHTML = "<button class=\\"pane-close\\" onclick=\\"closeDagPane()\\">✕</button>" + html;
      panel.classList.add("open");
      overlay.classList.add("open");
      panel.scrollTop = 0;
    }});
  }});
  
  // Close pane function
  window.closeDagPane = function() {{
    panel.classList.remove("open");
    overlay.classList.remove("open");
  }};
  
  // Click overlay to close
  overlay.addEventListener("click", function() {{
    panel.classList.remove("open");
    overlay.classList.remove("open");
  }});
}})();

function showLessonTab(topicId, idx) {{
  document.querySelectorAll(".dag-lesson[data-topic-id='" + topicId + "']").forEach(function(el, i) {{
    el.style.display = i === idx ? "" : "none";
  }});
  document.querySelectorAll(".dag-lesson-tab").forEach(function(tab, i) {{
    tab.classList.toggle("active", i === idx);
  }});
}}
</script>'''
    return svg


def _build_research_insights_html(checks: dict, ks_topics: dict) -> str:
    """Build research insights that connect efficiency/model findings to curriculum topics.
    
    Maps research data from other prompts back to relevant DAG topics,
    showing how real-world findings validate or extend what was learned.
    """
    insights = []
    
    # From efficiency_research — connect to optimization/engine topics
    eff = checks.get("efficiency_research", {})
    if isinstance(eff, dict):
        for key, val in eff.items():
            if not isinstance(val, dict):
                continue
            info = val.get("info", "")
            if not info or info == "No data":
                continue
            # Map to curriculum topics
            topic_links = []
            if any(k in key for k in ["quantiz", "quant", "gguf"]):
                topic_links = ["quantization_basics", "gguf_formats"]
            elif any(k in key for k in ["offload", "moe", "ktrans"]):
                topic_links = ["offloading_gpu_cpu_disk", "moe_expert_routing", "ktransformers"]
            elif any(k in key for k in ["specul", "decod"]):
                topic_links = ["speculative_decoding", "prefill_vs_decode"]
            elif any(k in key for k in ["vllm", "tensor", "engine"]):
                topic_links = ["vllm_tensorrt", "llama_cpp"]
            elif any(k in key for k in ["memory", "kv", "cache", "attention"]):
                topic_links = ["kv_cache_growth", "memory_bandwidth_vs_compute"]
            elif any(k in key for k in ["communit", "reddit", "local"]):
                topic_links = ["llama_cpp", "quantization_basics"]
            
            if topic_links:
                insights.append({
                    "source": "Efficiency Research",
                    "key": key.replace("_", " ").title(),
                    "info": info[:200],
                    "topics": topic_links,
                    "icon": "⚡",
                })
    
    # From model_benchmarks — connect to model topics
    bench = checks.get("model_benchmarks", {})
    if isinstance(bench, dict):
        models = bench.get("top_coding_models", [])
        if isinstance(models, list):
            for m in models[:3]:
                if not isinstance(m, dict):
                    continue
                name = m.get("name", "")
                arch = m.get("architecture", "")
                notes = m.get("notes", "")
                vram = m.get("vram_q4", "")
                topic_links = ["coding_model_traits"]
                if "moe" in arch.lower():
                    topic_links.append("dense_vs_moe")
                if vram:
                    topic_links.append("vram_calculation")
                insights.append({
                    "source": "Benchmark Data",
                    "key": name,
                    "info": f"{arch} — {notes}" if notes else f"{arch}, VRAM Q4: {vram}",
                    "topics": topic_links,
                    "icon": "🧠",
                })
    
    # From hardware — connect to hardware mapping topics
    hw = checks.get("hardware", {})
    if isinstance(hw, dict):
        for key, val in list(hw.items())[:4]:
            if not isinstance(val, dict):
                continue
            info = val.get("info", "")
            if not info:
                continue
            topic_links = []
            if "mac" in key or "unified" in key:
                topic_links = ["memory_bandwidth_vs_compute", "mlx_apple_silicon"]
            elif "gpu" in key or "rtx" in key or "4060" in key or "5090" in key:
                topic_links = ["vram_tiers_and_gpus", "pcie_lanes_multi_gpu"]
            elif "ram" in key:
                topic_links = ["ram_bandwidth_for_offload"]
            if topic_links:
                insights.append({
                    "source": "Hardware Monitor",
                    "key": key.replace("_", " ").title(),
                    "info": info[:150],
                    "topics": topic_links,
                    "icon": "🖥️",
                })
    
    if not insights:
        return ""
    
    html = '<div class="research-grid">'
    for ins in insights[:8]:
        topic_pills = ""
        for tid in ins["topics"][:3]:
            tinfo = TOPIC_DAG.get(tid, {})
            ts = ks_topics.get(tid, {})
            status = ts.get("status", "unseen")
            title = tinfo.get("title", tid.replace("_", " "))
            status_icon = {"unseen": "○", "introduced": "◐", "reinforced": "●", "applied": "✓"}.get(status, "○")
            topic_pills += (
                f'<span class="research-topic-link topic-{status}" '
                f'title="{_esc(title)}">{status_icon} {_esc(title[:25])}</span>'
            )
        
        html += (
            f'<div class="research-insight-card">'
            f'<div class="research-insight-source">{ins["icon"]} {_esc(ins["source"])}</div>'
            f'<div class="research-insight-key">{_esc(ins["key"])}</div>'
            f'<div class="research-insight-info">{_esc(ins["info"])}</div>'
            f'<div class="research-insight-topics">Related topics: {topic_pills}</div>'
            f'</div>'
        )
    html += '</div>'
    
    # CSS for research insights
    html += '''<style>
.research-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 12px; }
.research-insight-card { padding: 14px; background: var(--bg2); border-radius: 8px; border: 1px solid var(--border, #333); border-left: 3px solid var(--accent, #6366f1); }
.research-insight-source { font-size: 0.75rem; color: var(--dim); margin-bottom: 4px; }
.research-insight-key { font-size: 0.9rem; font-weight: 600; margin-bottom: 6px; }
.research-insight-info { font-size: 0.8rem; color: var(--dim); line-height: 1.4; margin-bottom: 8px; }
.research-insight-topics { display: flex; flex-wrap: wrap; gap: 4px; }
.research-topic-link { font-size: 0.7rem; padding: 2px 8px; border-radius: 10px; border: 1px solid var(--border, #444); }
</style>'''
    return html


def _generate_learning_page(checks, enrichment, now, state=None):
    """Generate the learning feed page with curriculum progress and lesson cards."""
    nav_html = _generate_nav_html("learning", now)

    # Knowledge state for curriculum display
    ks = (state or {}).get("knowledge_state", {})
    ks_topics = ks.get("topics", {})
    total_topics = len(TOPIC_DAG) if TOPIC_DAG else 34
    learned_count = sum(1 for t in ks_topics.values() if t.get("status") != "unseen")
    progress_pct = int(100 * learned_count / total_topics) if total_topics else 0
    
    # Curriculum position
    completed_layers = ks.get("curriculum_position", {}).get("completed_layers", [])
    total_lessons = ks.get("total_lessons_completed", 0)
    
    # Build curriculum progress HTML
    layer_names = {
        0: "Foundations", 1: "Core Inference", 2: "Optimization",
        3: "Engines & Runtimes", 4: "Models", 5: "Agents & Application",
        6: "Hardware Mapping", 7: "Fine-tuning"
    }
    layer_icons = {
        0: "🏗️", 1: "⚡", 2: "🔧", 3: "🚀", 4: "🧠", 5: "🤖", 6: "🖥️", 7: "🎯"
    }
    
    # Generate SVG DAG visualization
    dag_viz_html = _generate_dag_visualization(ks_topics)
    
    # Build recent lessons from the learning_feed checks data
    lessons_html = ""
    lf = checks.get("learning_feed", {})
    lessons_data = lf.get("lessons", [])
    # Handle flat dict format: single lesson at top level
    if not lessons_data and isinstance(lf, dict):
        if "topic_id" in lf or "content" in lf:
            lessons_data = [lf]
        else:
            for key, val in lf.items():
                if isinstance(val, dict) and ("content" in val or "title" in val):
                    lessons_data.append(val)
                elif isinstance(val, list):
                    for item in val:
                        if isinstance(item, dict) and ("content" in item or "title" in item):
                            lessons_data.append(item)
    
    for lesson in lessons_data:
        if not isinstance(lesson, dict):
            continue
        title = _esc(lesson.get("title", "Untitled Lesson"))
        tid = _esc(lesson.get("topic_id", ""))
        content = _esc(lesson.get("content", ""))
        takeaways = lesson.get("key_takeaways", [])
        recap = _esc(lesson.get("prerequisite_recap", ""))
        exercise = _esc(lesson.get("practical_exercise", ""))
        answer = _esc(lesson.get("answer", ""))
        hw_impl = _esc(lesson.get("hardware_implication", ""))
        reliability = lesson.get("reliability", "stable")
        rel_cls = f"rel-{reliability}"
        rel_icon = {"stable": "✅", "emerging": "🔶", "experimental": "🧪"}.get(reliability, "✅")
        
        takeaways_html = ""
        for t in takeaways:
            if isinstance(t, str):
                takeaways_html += f'<li>{_esc(t)}</li>'
        
        lessons_html += (
            f'<div class="lesson-card">'
            f'<div class="lesson-header">'
            f'<h3 class="lesson-title">📖 {title}</h3>'
            f'<span class="reliability-badge {rel_cls}">{rel_icon} {reliability.title()}</span>'
            f'</div>'
        )
        if recap:
            lessons_html += f'<div class="lesson-recap">🔗 <em>Previously: {recap}</em></div>'
        if content:
            # Show first 500 chars with expand
            short = content[:500]
            if len(content) > 500:
                lessons_html += (
                    f'<div class="lesson-content">{short}...'
                    f'<button class="expand-btn" onclick="this.parentElement.textContent=this.parentElement.dataset.full" '
                    f'data-full="">Read more</button></div>'
                    f'<div class="lesson-full" style="display:none">{content}</div>'
                )
            else:
                lessons_html += f'<div class="lesson-content">{content}</div>'
        if takeaways_html:
            lessons_html += f'<div class="lesson-takeaways"><strong>Key Takeaways:</strong><ul>{takeaways_html}</ul></div>'
        if exercise:
            lessons_html += (
                f'<div class="lesson-exercise">'
                f'<strong>🧮 Exercise:</strong> {exercise}'
                f'<details><summary>Show Answer</summary><p>{answer}</p></details>'
                f'</div>'
            )
        if hw_impl:
            lessons_html += f'<div class="lesson-hw">🖥️ <strong>Hardware Implication:</strong> {hw_impl}</div>'
        lessons_html += '</div>'

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

    # Enhanced CSS for curriculum view
    curriculum_css = """
<style>
.progress-hero { background: linear-gradient(135deg, #1e1e2e, #2d2d44); border-radius: 12px; padding: 24px; margin-bottom: 24px; display: grid; grid-template-columns: auto 1fr; gap: 24px; align-items: center; }
.progress-circle { width: 100px; height: 100px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 2rem; font-weight: bold; color: #10b981; }
.progress-stats { display: flex; flex-direction: column; gap: 4px; }
.progress-bar-bg { height: 12px; background: #333; border-radius: 6px; overflow: hidden; margin: 8px 0; }
.progress-bar-fill { height: 100%; background: linear-gradient(90deg, #10b981, #6366f1); border-radius: 6px; transition: width 0.5s; }
.dag-section { margin-bottom: 24px; }
.dag-layer { padding: 12px 16px; margin-bottom: 8px; border-radius: 8px; border: 1px solid var(--border, #333); background: var(--bg2); }
.dag-layer.layer-complete { border-color: #10b981; }
.layer-header { font-size: 0.95rem; margin-bottom: 8px; }
.layer-topics { display: flex; flex-wrap: wrap; gap: 6px; }
.topic-pill { font-size: 0.75rem; padding: 3px 10px; border-radius: 12px; border: 1px solid var(--border, #444); white-space: nowrap; }
.topic-unseen { color: var(--dim); border-color: #555; }
.topic-introduced { color: #f59e0b; border-color: #f59e0b; background: rgba(245,158,11,0.1); }
.topic-reinforced { color: #10b981; border-color: #10b981; background: rgba(16,185,129,0.1); }
.topic-applied { color: #6366f1; border-color: #6366f1; background: rgba(99,102,241,0.15); font-weight: 600; }
.lessons-section { margin-top: 24px; }
.lesson-card { background: var(--bg2); border: 1px solid var(--border, #333); border-radius: 10px; padding: 20px; margin-bottom: 16px; }
.lesson-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; flex-wrap: wrap; gap: 8px; }
.lesson-title { font-size: 1.1rem; margin: 0; }
.reliability-badge { font-size: 0.75rem; padding: 2px 8px; border-radius: 4px; }
.rel-stable { background: #10b981; color: white; }
.rel-emerging { background: #f59e0b; color: black; }
.rel-experimental { background: #8b5cf6; color: white; }
.lesson-recap { font-size: 0.85rem; color: var(--dim); margin-bottom: 12px; padding: 8px 12px; background: rgba(99,102,241,0.08); border-radius: 6px; }
.lesson-content { font-size: 0.9rem; line-height: 1.6; color: var(--fg); margin-bottom: 12px; white-space: pre-wrap; }
.lesson-takeaways { margin-bottom: 12px; }
.lesson-takeaways ul { margin: 4px 0 0 16px; padding: 0; }
.lesson-takeaways li { font-size: 0.85rem; margin-bottom: 4px; color: #10b981; }
.lesson-exercise { background: rgba(245,158,11,0.08); padding: 12px; border-radius: 8px; margin-bottom: 12px; font-size: 0.9rem; }
.lesson-exercise details { margin-top: 8px; }
.lesson-exercise summary { cursor: pointer; color: var(--accent); font-size: 0.85rem; }
.lesson-exercise p { margin: 8px 0 0; color: #10b981; }
.lesson-hw { font-size: 0.85rem; padding: 8px 12px; background: rgba(16,185,129,0.08); border-radius: 6px; }
.expand-btn { background: none; border: none; color: var(--accent); cursor: pointer; font-size: 0.85rem; }
.section-title { font-size: 1.2rem; margin: 24px 0 12px; border-bottom: 1px solid var(--border, #333); padding-bottom: 8px; }
@media (max-width: 768px) {
  .progress-hero { grid-template-columns: 1fr; text-align: center; }
  .dag-layer { padding: 8px 12px; }
  .topic-pill { font-size: 0.7rem; padding: 2px 6px; }
  .lesson-card { padding: 12px; }
}
</style>
"""

    # Also keep original learning CSS for article cards
    full_css = curriculum_css + learning_css

    body_content = (
        full_css
        + '\n<div class="header">'
        '\n  <div class="header-top">'
        '\n    <h1>\U0001F5A5\uFE0F LLM Homelab</h1>'
        f'\n    <div class="meta">Last check: <b>{now}</b></div>'
        '\n  </div>'
        '\n</div>'
        '\n'
        '\n<div class="content">'
        '\n  <div class="page-title">\U0001F4DA Progressive Knowledge Builder</div>'
        '\n  <div class="page-desc">Building your understanding of local LLM inference, step by step. Each run teaches new topics and unlocks advanced concepts.</div>'
        '\n'
        # Progress hero
        '\n  <div class="progress-hero">'
        f'\n    <div class="progress-circle">{progress_pct}%</div>'
        '\n    <div class="progress-stats">'
        f'\n      <strong>{learned_count} / {total_topics} topics covered</strong>'
        f'\n      <div class="progress-bar-bg"><div class="progress-bar-fill" style="width:{progress_pct}%"></div></div>'
        f'\n      <span style="color:var(--dim);font-size:0.85rem">{total_lessons} lessons completed &middot; '
        f'Layers done: {len(completed_layers)}/8</span>'
        '\n    </div>'
        '\n  </div>'
        '\n'
        # Topic DAG
        '\n  <div class="dag-section">'
        '\n    <div class="section-title">\U0001F5FA\uFE0F Knowledge Map</div>'
        f'\n    {dag_viz_html}'
        '\n  </div>'
        '\n'
    )

    # Latest lessons section
    if lessons_html:
        body_content += (
            '\n  <div class="lessons-section">'
            '\n    <div class="section-title">\U0001F4D6 Today\'s Lessons</div>'
            f'\n    {lessons_html}'
            '\n  </div>'
        )
    
    # Research Insights section — connects research findings to curriculum topics
    research_html = _build_research_insights_html(checks, ks_topics)
    if research_html:
        body_content += (
            '\n  <div class="research-section">'
            '\n    <div class="section-title">🔬 Research Insights (Cross-pollination)</div>'
            '\n    <div style="font-size:0.8rem;color:var(--dim);margin-bottom:12px">'
            'Findings from hardware, efficiency, and model research that connect to your curriculum</div>'
            f'\n    {research_html}'
            '\n  </div>'
        )
    
    # Legacy article cards (if any articles still exist from old format)
    if cards_html:
        body_content += (
            '\n  <div class="section-title">\U0001F4F0 Latest Articles & Resources</div>'
            '\n  <div class="filter-bar filter-category"><strong>Category:</strong>' + cat_btns + '</div>'
            '\n  <div class="filter-bar filter-source"><strong>Source:</strong>' + src_btns + '</div>'
            f'\n  {cards_inner}'
        )
    elif not lessons_html:
        body_content += (
            '\n  <div class="empty-state">'
            'No lessons yet. Run the monitor to start learning! The system will teach '
            'foundational topics first (tokens, parameters, VRAM), then progressively unlock '
            'advanced topics (quantization, offloading, engines, models, agents).'
            '</div>'
        )

    body_content += (
        '\n</div>'
        '\n<div id="copy-toast" class="copy-toast">Copied!</div>'
        + learning_js
    )

    modal_json = json.dumps({}, ensure_ascii=False)
    return _generate_page_shell("Knowledge Builder - LLM Homelab", nav_html, body_content, modal_json)


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
        '\n    <h1>\U0001F5A5\uFE0F LLM Homelab</h1>'
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
    return _generate_page_shell("Weekly Report", nav_html, body_content, modal_json)


def _generate_timeline_page(state: dict, now: str) -> str:
    """Generate the analytical timeline/changelog page showing how analysis evolved."""
    nav_html = _generate_nav_html("timeline", now)
    
    changelog = state.get("changelog", [])
    analytical_state = state.get("analytical_state", {})
    
    # Domain color mapping
    domain_colors = {
        "hardware": "#3b82f6",
        "models": "#8b5cf6",
        "optimization": "#10b981",
        "setup": "#f59e0b",
    }
    
    # Build timeline entries (newest first)
    entries_html = ""
    if changelog:
        sorted_log = sorted(changelog, key=lambda x: x.get("date", ""), reverse=True)
        for entry in sorted_log[:50]:
            domain = entry.get("domain", "general")
            color = domain_colors.get(domain, "#6b7280")
            date = entry.get("date", "Unknown")
            change = _esc(entry.get("change", ""))
            reason = _esc(entry.get("reason", ""))
            evidence = entry.get("evidence", [])
            ev_count = len(evidence) if isinstance(evidence, list) else 0
            
            entries_html += (
                f'<div class="timeline-entry" style="border-left: 3px solid {color}; padding-left: 16px; margin-bottom: 16px;">'
                f'  <div style="display:flex; gap:12px; align-items:center;">'
                f'    <span style="font-size:0.8rem; color:var(--dim);">{_esc(date)}</span>'
                f'    <span style="background:{color}22; color:{color}; padding:2px 8px; border-radius:4px; font-size:0.75rem;">{_esc(domain)}</span>'
                f'    <span style="font-size:0.75rem; color:var(--dim);">{ev_count} evidence</span>'
                f'  </div>'
                f'  <div style="margin-top:6px; font-weight:500;">{change}</div>'
                f'  <div style="margin-top:4px; font-size:0.85rem; color:var(--dim);">{reason}</div>'
                f'</div>'
            )
    else:
        entries_html = '<p style="color:var(--dim);">No changelog entries yet. Run the pipeline to generate analytical history.</p>'
    
    # Confidence evolution section
    confidence_html = ""
    for domain, ds in analytical_state.items():
        conf = ds.get("confidence", 0)
        color = domain_colors.get(domain, "#6b7280")
        bar_width = int(conf * 100)
        confidence_html += (
            f'<div style="display:flex; align-items:center; gap:8px; margin-bottom:8px;">'
            f'  <span style="width:100px; font-size:0.85rem;">{_esc(domain.title())}</span>'
            f'  <div style="flex:1; height:8px; background:var(--bg); border-radius:4px; overflow:hidden;">'
            f'    <div style="width:{bar_width}%; height:100%; background:{color}; border-radius:4px;"></div>'
            f'  </div>'
            f'  <span style="width:40px; text-align:right; font-size:0.85rem; font-weight:600;">{bar_width}%</span>'
            f'</div>'
        )
    
    body_content = (
        '<div class="container" style="max-width:900px; margin:auto; padding:24px;">'
        '<h1 style="margin-bottom:8px;">Analysis Timeline</h1>'
        '<p style="color:var(--dim); margin-bottom:24px;">How our analytical worldview evolved over time</p>'
        '<div style="background:var(--bg2); border-radius:12px; padding:20px; margin-bottom:24px;">'
        '<h3 style="margin-bottom:12px;">Current Confidence Levels</h3>'
        + confidence_html +
        '</div>'
        '<h3 style="margin-bottom:16px;">Changelog</h3>'
        + entries_html +
        '</div>'
    )
    
    modal_json = json.dumps({}, ensure_ascii=False, default=str)
    return _generate_page_shell("Timeline", nav_html, body_content, modal_json)


def _generate_ask_page(state: dict, now: str) -> str:
    """Generate the Ask page showing pending/answered questions."""
    nav_html = _generate_nav_html("ask", now)
    
    questions = state.get("questions", {"pending": [], "answered": []})
    pending = questions.get("pending", [])
    answered = questions.get("answered", [])
    
    # Pending questions
    pending_html = ""
    if pending:
        for q in pending:
            question_text = _esc(q.get("q", q) if isinstance(q, dict) else str(q))
            submitted = q.get("submitted", "") if isinstance(q, dict) else ""
            pending_html += (
                f'<div style="padding:12px 16px; background:var(--bg); border-radius:8px; margin-bottom:8px; border-left:3px solid #f59e0b;">'
                f'  <div style="font-weight:500;">{question_text}</div>'
                f'  <div style="font-size:0.8rem; color:var(--dim); margin-top:4px;">Submitted: {_esc(submitted)} &middot; Will be answered on next pipeline run</div>'
                f'</div>'
            )
    else:
        pending_html = '<p style="color:var(--dim);">No pending questions.</p>'
    
    # Answered questions
    answered_html = ""
    if answered:
        for a in answered[-10:]:  # Show last 10
            question_text = _esc(a.get("q", ""))
            answer_text = _esc(a.get("answer", ""))
            answered_on = _esc(a.get("answered_on", ""))
            confidence = a.get("confidence", "")
            answered_html += (
                f'<div style="padding:16px; background:var(--bg); border-radius:8px; margin-bottom:12px; border-left:3px solid #10b981;">'
                f'  <div style="font-weight:600; margin-bottom:8px;">{question_text}</div>'
                f'  <div style="font-size:0.9rem; line-height:1.5;">{answer_text}</div>'
                f'  <div style="font-size:0.8rem; color:var(--dim); margin-top:8px;">Answered: {answered_on} &middot; Confidence: {confidence}</div>'
                f'</div>'
            )
    else:
        answered_html = '<p style="color:var(--dim);">No answered questions yet.</p>'
    
    body_content = (
        '<div class="container" style="max-width:900px; margin:auto; padding:24px;">'
        '<h1 style="margin-bottom:8px;">Ask LLM Homelab</h1>'
        '<p style="color:var(--dim); margin-bottom:24px;">'
        'Submit questions about local LLM setup — answered with full analytical rigor on the next pipeline run. '
        'To ask a question, create a GitHub Issue with the label <code>homelab-question</code>.</p>'
        '<div style="background:var(--bg2); border-radius:12px; padding:20px; margin-bottom:24px;">'
        '<h3 style="margin-bottom:12px;">Pending Questions (' + str(len(pending)) + ')</h3>'
        + pending_html +
        '</div>'
        '<div style="background:var(--bg2); border-radius:12px; padding:20px;">'
        '<h3 style="margin-bottom:12px;">Answered (' + str(len(answered)) + ')</h3>'
        + answered_html +
        '</div></div>'
    )
    
    modal_json = json.dumps({}, ensure_ascii=False, default=str)
    return _generate_page_shell("Ask", nav_html, body_content, modal_json)


def _generate_situation_room(state: dict, now: str) -> str:
    """Generate the Situation Room landing page (index.html)."""
    nav_html = _generate_nav_html("summary", now)
    analytical_state = state.get("analytical_state", {})
    changelog = state.get("changelog", [])
    questions = state.get("questions", {"pending": [], "answered": []})
    rec = state.get("recommendation", {})
    pipeline_meta = state.get("pipeline_meta", {})

    domain_icons = {"hardware": "🖥️", "models": "🧠", "optimization": "🔬", "setup": "⚙️"}
    domain_colors = {"hardware": "#3b82f6", "models": "#8b5cf6", "optimization": "#10b981", "setup": "#f59e0b"}

    # 1. Current Analysis Summary (the main content — what we know NOW)
    analysis_summary_html = '<div style="margin-bottom:32px;">'
    analysis_summary_html += '<h3 style="margin-bottom:16px;font-size:1.1em;">📊 Latest Analysis</h3>'
    for domain in ("hardware", "models", "optimization", "setup"):
        ds = analytical_state.get(domain, {})
        analysis_text = ds.get("current_analysis", "Awaiting first pipeline run.")
        conf = int(ds.get("confidence", 0) * 100)
        icon = domain_icons.get(domain, "📊")
        color = domain_colors.get(domain, "#6b7280")
        # Truncate to first 400 chars for summary
        summary = _esc(analysis_text[:400]) + ("..." if len(analysis_text) > 400 else "")
        analysis_summary_html += (
            f'<div style="margin-bottom:16px;padding:16px;background:var(--card);border-radius:10px;border-left:4px solid {color};">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">'
            f'<span style="font-weight:700;color:{color};">{icon} {domain.title()}</span>'
            f'<span style="font-size:0.8em;color:{color};background:rgba(255,255,255,0.05);padding:2px 10px;border-radius:10px;">{conf}% confidence</span>'
            f'</div>'
            f'<p style="font-size:0.88em;line-height:1.7;color:var(--text);margin:0;">{summary}</p>'
            f'<a href="pages/{domain}.html" style="display:inline-block;margin-top:8px;font-size:0.8em;color:var(--accent);text-decoration:none;">View full analysis →</a>'
            f'</div>'
        )
    analysis_summary_html += '</div>'

    # 2. Current Recommendation
    rec_action = rec.get("recommendation", "wait")
    rec_best = _esc(rec.get("best_option", "No recommendation yet"))
    rec_reasoning = _esc(str(rec.get("reasoning", ""))[:300])
    rec_confidence = rec.get("confidence", "medium")
    rec_links = rec.get("buy_links", [])

    action_colors = {"buy_now": "var(--green)", "wait": "var(--yellow)", "consider_alternative": "var(--accent)"}
    action_labels = {"buy_now": "✅ BUY NOW", "wait": "⏳ WAIT", "consider_alternative": "🔄 CONSIDER ALT"}
    rec_color = action_colors.get(rec_action, "var(--dim)")
    rec_label = action_labels.get(rec_action, "⏳ WAIT")

    buy_links_html = ""
    if rec_links and isinstance(rec_links, list):
        for lnk in rec_links[:3]:
            if isinstance(lnk, dict):
                url = _esc(lnk.get("url", "#"))
                title = _esc(lnk.get("title", "Buy"))
                buy_links_html += f' <a href="{url}" target="_blank" style="color:var(--accent);font-size:0.82em;margin-left:8px;">🛒 {title}</a>'

    rec_html = (
        f'<div class="rec-card" style="cursor:default;">'
        f'<div class="rec-top"><span class="rec-badge {_esc(rec_action)}">{rec_label}</span>'
        f'<span style="font-size:0.82em;color:var(--dim)">Confidence: {_esc(str(rec_confidence))}</span></div>'
        f'<div class="rec-title">{rec_best}</div>'
        f'<div class="rec-summary">{rec_reasoning}</div>'
        f'{buy_links_html}'
        f'</div>'
    )

    # 3. Domain Confidence Cards
    cards_html = '<div class="sr-grid">'
    for domain in ("hardware", "models", "optimization", "setup"):
        ds = analytical_state.get(domain, {})
        conf = ds.get("confidence", 0)
        options = ds.get("options", [])
        evidence = ds.get("evidence", [])
        last_changed = ds.get("last_changed", "—")
        top_opt = options[0].get("name", "—") if options else "—"
        icon = domain_icons.get(domain, "📊")
        color = domain_colors.get(domain, "#6b7280")
        conf_pct = int(conf * 100)

        # SVG confidence ring
        circumference = 2 * 3.14159 * 24
        dash = conf * circumference
        gap = circumference - dash
        ring_svg = (
            f'<svg class="confidence-ring" viewBox="0 0 64 64">'
            f'<circle cx="32" cy="32" r="24" fill="none" stroke="var(--border)" stroke-width="5"/>'
            f'<circle cx="32" cy="32" r="24" fill="none" stroke="{color}" stroke-width="5" '
            f'stroke-dasharray="{dash:.1f} {gap:.1f}" stroke-linecap="round" '
            f'transform="rotate(-90 32 32)"/>'
            f'<text x="32" y="36" text-anchor="middle" font-size="14" font-weight="700" fill="{color}">{conf_pct}</text>'
            f'</svg>'
        )

        cards_html += (
            f'<a href="pages/{domain}.html" class="sr-card" style="text-decoration:none;color:inherit;">'
            f'<div class="domain-label">{icon} {_esc(domain.title())}</div>'
            f'{ring_svg}'
            f'<div class="top-option">{_esc(top_opt)}</div>'
            f'<div class="ev-count">{len(evidence)} evidence items</div>'
            f'<div class="last-updated">Changed: {_esc(str(last_changed)[:10])}</div>'
            f'</a>'
        )
    cards_html += '</div>'

    # 4. Pending Questions
    pending = questions.get("pending", [])
    pending_html = ""
    if pending:
        pending_html = '<div style="margin-bottom:24px;"><h3 style="margin-bottom:12px;">❓ Pending Questions</h3>'
        for q in pending[:5]:
            qt = _esc(q.get("q", q) if isinstance(q, dict) else str(q))
            pending_html += f'<div style="padding:8px 12px;background:var(--card);border-radius:8px;margin-bottom:6px;font-size:0.9em;border-left:3px solid var(--yellow)">{qt}</div>'
        pending_html += '</div>'

    # 5. Pipeline Status
    last_run = state.get("last_run", "Never")
    critique = pipeline_meta.get("last_critique", {})
    quality_score = critique.get("quality_score", "—")
    pipeline_html = (
        f'<div class="sr-pipeline">'
        f'<h3 style="margin-bottom:8px;">🔧 Pipeline Status</h3>'
        f'<div style="display:flex;gap:24px;flex-wrap:wrap;">'
        f'<div><span style="color:var(--dim);">Last run:</span> <b>{_esc(str(last_run)[:19])}</b></div>'
        f'<div><span style="color:var(--dim);">Quality score:</span> <b style="color:var(--accent);">{_esc(str(quality_score))}</b></div>'
        f'</div></div>'
    )

    body_content = (
        '\n<div class="header">'
        '\n  <div class="header-top">'
        '\n    <h1>🏠 Situation Room</h1>'
        f'\n    <div class="meta">Last updated: <b>{_esc(now)}</b></div>'
        '\n  </div>'
        '\n</div>'
        '\n<div class="content">'
        f'\n  {rec_html}'
        f'\n  {analysis_summary_html}'
        f'\n  {cards_html}'
        f'\n  {pending_html}'
        f'\n  {pipeline_html}'
        '\n</div>'
    )

    modal_json = json.dumps({}, ensure_ascii=False, default=str)
    return _generate_page_shell("Situation Room", nav_html, body_content, modal_json)


def _generate_analysis_page(state: dict, domain: str, now: str) -> str:
    """Generate an analysis page for a given domain (hardware/models/optimization/setup)."""
    nav_html = _generate_nav_html(domain, now)
    analytical_state = state.get("analytical_state", {})
    ds = analytical_state.get(domain, {})
    changelog = state.get("changelog", [])

    domain_icons = {"hardware": "🖥️", "models": "🧠", "optimization": "🔬", "setup": "⚙️"}
    domain_titles = {"hardware": "Hardware Analysis", "models": "Models Analysis", "optimization": "Optimization Analysis", "setup": "Setup Analysis"}
    icon = domain_icons.get(domain, "📊")
    title = domain_titles.get(domain, f"{domain.title()} Analysis")

    # 1. Current Position
    current_analysis = _esc(ds.get("current_analysis", "No analysis available yet. Run the pipeline to generate."))
    position_html = (
        f'<div class="analysis-position">'
        f'<h3 style="margin-bottom:12px;color:var(--accent);">Current Position</h3>'
        f'<p>{current_analysis}</p>'
        f'</div>'
    )

    # 2. Confidence & Evidence
    conf = ds.get("confidence", 0)
    evidence = ds.get("evidence", [])
    conf_pct = int(conf * 100)
    domain_colors = {"hardware": "#3b82f6", "models": "#8b5cf6", "optimization": "#10b981", "setup": "#f59e0b"}
    color = domain_colors.get(domain, "#6b7280")
    circumference = 2 * 3.14159 * 24
    dash = conf * circumference
    gap = circumference - dash
    ring_svg = (
        f'<svg class="confidence-ring" viewBox="0 0 64 64">'
        f'<circle cx="32" cy="32" r="24" fill="none" stroke="var(--border)" stroke-width="5"/>'
        f'<circle cx="32" cy="32" r="24" fill="none" stroke="{color}" stroke-width="5" '
        f'stroke-dasharray="{dash:.1f} {gap:.1f}" stroke-linecap="round" '
        f'transform="rotate(-90 32 32)"/>'
        f'<text x="32" y="36" text-anchor="middle" font-size="14" font-weight="700" fill="{color}">{conf_pct}</text>'
        f'</svg>'
    )

    session_name = _session_name_for(domain)
    conf_html = (
        f'<div class="confidence-bar-wrap">'
        f'{ring_svg}'
        f'<div class="confidence-info">'
        f'<div class="label">Confidence Level</div>'
        f'<div class="value" style="color:{color}">{conf_pct}%</div>'
        f'<div class="meta">{len(evidence)} evidence items collected</div>'
        f'</div>'
        f'<button class="discuss-cli-btn" onclick="navigator.clipboard.writeText(\'copilot --resume=\\\"{_esc(session_name)}\\\"\').then(()=>alert(\'Copied!\'))">💬 Discuss in CLI</button>'
        f'</div>'
    )

    # 3. Options Matrix
    options = ds.get("options", [])
    matrix_html = ""
    if options:
        matrix_html = (
            '<h3 style="margin-bottom:12px;color:var(--accent);">Options Matrix</h3>'
            '<div style="overflow-x:auto;margin-bottom:24px;">'
            '<table class="options-matrix">'
            '<thead><tr><th>Rank</th><th>Name</th><th>Pros</th><th>Cons</th><th>Cost</th><th>Performance</th></tr></thead>'
            '<tbody>'
        )
        for opt in sorted(options, key=lambda x: x.get("recommendation_rank", 99)):
            rank = opt.get("recommendation_rank", "—")
            name = _esc(opt.get("name", ""))
            pros = opt.get("pros", [])
            cons = opt.get("cons", [])
            cost = _esc(opt.get("cost", "—"))
            perf = _esc(opt.get("performance", "—"))
            pros_html = "<br>".join(f"✅ {_esc(p)}" for p in (pros if isinstance(pros, list) else []))
            cons_html = "<br>".join(f"❌ {_esc(c)}" for c in (cons if isinstance(cons, list) else []))
            rank_cls = f"rank-{rank}" if isinstance(rank, int) and rank <= 2 else ""
            matrix_html += (
                f'<tr class="{rank_cls}">'
                f'<td><b>#{rank}</b></td>'
                f'<td><b>{name}</b></td>'
                f'<td class="pros">{pros_html}</td>'
                f'<td class="cons">{cons_html}</td>'
                f'<td>{cost}</td>'
                f'<td>{perf}</td>'
                f'</tr>'
            )
        matrix_html += '</tbody></table></div>'

    # 4. Evidence Trail
    ev_html = ""
    if evidence:
        ev_html = '<h3 style="margin-bottom:12px;color:var(--accent);">Evidence Trail</h3><ul class="evidence-list">'
        source_icons = {"benchmark": "🔬", "community": "👥", "official": "📋", "research": "📖", "price": "💰"}
        for ev in evidence:
            if isinstance(ev, dict):
                source = ev.get("source", "other")
                text = _esc(ev.get("text", str(ev)))
                ev_icon = source_icons.get(source, "📌")
            else:
                text = _esc(str(ev))
                ev_icon = "📌"
            ev_html += f'<li><span class="ev-icon">{ev_icon}</span><span class="ev-text">{text}</span></li>'
        ev_html += '</ul>'

    # 5. Conflicts Resolved
    conflicts = ds.get("conflicts_resolved", [])
    conflicts_html = ""
    if conflicts:
        conflicts_html = '<h3 style="margin-bottom:12px;color:var(--accent);">Conflicts Resolved</h3><div class="conflicts-list">'
        for c in conflicts:
            if isinstance(c, dict):
                text = _esc(c.get("conflict", str(c)))
                resolution = _esc(c.get("resolution", ""))
                conflicts_html += f'<div class="conflict-item"><div style="font-weight:500;">{text}</div><div style="font-size:0.85em;color:var(--dim);margin-top:4px;">Resolution: {resolution}</div></div>'
            else:
                conflicts_html += f'<div class="conflict-item">{_esc(str(c))}</div>'
        conflicts_html += '</div>'

    # 6. What Changed (domain-specific changelog)
    domain_log = [e for e in changelog if e.get("domain") == domain]
    domain_log_html = ""
    if domain_log:
        domain_log_html = '<h3 style="margin-bottom:12px;color:var(--accent);">What Changed</h3><div class="domain-changelog">'
        for entry in sorted(domain_log, key=lambda x: x.get("date", ""), reverse=True)[:10]:
            date = _esc(entry.get("date", ""))
            change = _esc(entry.get("change", ""))
            reason = _esc(entry.get("reason", ""))
            domain_log_html += (
                f'<div class="entry">'
                f'<div class="entry-date">{date}</div>'
                f'<div class="entry-text"><b>{change}</b></div>'
                f'<div style="font-size:0.82em;color:var(--dim);margin-top:2px;">{reason}</div>'
                f'</div>'
            )
        domain_log_html += '</div>'

    body_content = (
        '\n<div class="header">'
        '\n  <div class="header-top">'
        f'\n    <h1>{icon} {_esc(title)}</h1>'
        f'\n    <div class="meta">Last check: <b>{_esc(now)}</b></div>'
        '\n  </div>'
        '\n</div>'
        '\n<div class="content">'
        f'\n  {position_html}'
        f'\n  {conf_html}'
        f'\n  {matrix_html}'
        f'\n  {ev_html}'
        f'\n  {conflicts_html}'
        f'\n  {domain_log_html}'
        '\n</div>'
    )

    modal_json = json.dumps({}, ensure_ascii=False, default=str)
    return _generate_page_shell(f"{title} - LLM Homelab", nav_html, body_content, modal_json)


def _generate_knowledge_graph_page(state: dict, now: str) -> str:
    """Generate unified Knowledge Graph: learning topics + research + hardware as interconnected nodes."""
    nav_html = _generate_nav_html("knowledge", now)
    ks = state.get("knowledge_state", {})
    ks_topics = ks.get("topics", {})
    analytical_state = state.get("analytical_state", {})

    # ── Build unified node set ──
    # Type 1: Learning topics (from TOPIC_DAG)
    # Type 2: Domain analysis nodes (hardware, models, optimization, setup)
    # Type 3: Research findings (top options from each domain)

    DOMAIN_COLORS = {"hardware": "#3b82f6", "models": "#8b5cf6", "optimization": "#10b981", "setup": "#f59e0b"}
    DOMAIN_ICONS = {"hardware": "🖥️", "models": "🧠", "optimization": "🔬", "setup": "⚙️"}
    LEARNING_COLOR = "#6366f1"
    FINDING_COLOR = "#f472b6"

    # Map learning topics to related domains
    TOPIC_DOMAIN_LINKS = {
        "vram_calculation": "hardware", "vram_tiers_and_gpus": "hardware",
        "pcie_lanes_multi_gpu": "hardware", "ram_bandwidth_for_offload": "hardware",
        "ssd_weight_loading": "hardware", "power_thermals_noise": "hardware",
        "tokens_and_parameters": "models", "dense_vs_moe": "models",
        "coding_model_traits": "models", "model_selection_for_agents": "models",
        "quantization_basics": "optimization", "gguf_formats": "optimization",
        "exl2_awq_gptq": "optimization", "offloading_gpu_cpu_disk": "optimization",
        "speculative_decoding": "optimization", "moe_expert_routing": "optimization",
        "llama_cpp": "setup", "ktransformers": "setup", "vllm_tensorrt": "setup",
        "mlx_apple_silicon": "setup", "runtime_compat": "setup", "os_runtime_friction": "setup",
    }

    SHORT_LABELS = {
        "tokens_and_parameters": "Tokens & Params", "vram_calculation": "VRAM Calc",
        "context_window_math": "Context Windows", "prefill_vs_decode": "Prefill/Decode",
        "kv_cache_growth": "KV Cache", "memory_bandwidth_vs_compute": "Mem Bandwidth",
        "latency_vs_throughput": "Latency/Throughput", "quantization_basics": "Quantization",
        "gguf_formats": "GGUF Formats", "exl2_awq_gptq": "EXL2/AWQ/GPTQ",
        "offloading_gpu_cpu_disk": "Offloading", "moe_expert_routing": "MoE Routing",
        "speculative_decoding": "Spec Decoding", "llama_cpp": "llama.cpp",
        "ktransformers": "KTransformers", "vllm_tensorrt": "vLLM/TensorRT",
        "mlx_apple_silicon": "MLX (Apple)", "runtime_compat": "Runtime Compat",
        "dense_vs_moe": "Dense vs MoE", "coding_model_traits": "Coding Models",
        "reasoning_chains": "Reasoning", "model_selection_for_agents": "Model Selection",
        "agent_context_management": "Agent Context", "yolo_coding_mode": "YOLO Mode",
        "batch_concurrency": "Batch/Concurrency", "tool_use_function_calling": "Tool Use",
        "vram_tiers_and_gpus": "GPU VRAM Tiers", "ram_bandwidth_for_offload": "RAM Bandwidth",
        "pcie_lanes_multi_gpu": "PCIe/Multi-GPU", "ssd_weight_loading": "SSD Loading",
        "power_thermals_noise": "Power/Thermals", "os_runtime_friction": "OS/Runtime",
        "lora_qlora_basics": "LoRA/QLoRA", "when_to_finetune": "When to Fine-tune",
    }

    # ── Layout: Clustered by domain (4 columns), domain hub at top of each column ──
    # Each column contains: Domain Hub → Finding pills (stacked) → Related learning topics
    # Unlinked topics go in a center "General" cluster below

    STATUS_COLORS = {"unseen": "#555", "introduced": "#f59e0b", "reinforced": "#10b981", "applied": "#6366f1"}

    # Column layout — wider columns, findings stacked vertically
    domains = list(DOMAIN_COLORS.keys())
    col_width = 360
    svg_width = col_width * 4 + 100  # 4 columns + margins
    col_margin = 50
    domain_y = 70
    findings_start_y = 145
    finding_v_gap = 36  # vertical gap between findings
    node_w, node_h = 130, 38
    v_gap_topic = 54

    # Position domain hub nodes (centered in each column)
    domain_positions = {}
    for i, d in enumerate(domains):
        cx = col_margin + i * col_width + col_width / 2
        domain_positions[d] = (cx, domain_y)

    # Position findings stacked vertically under each domain hub
    finding_nodes = []
    max_findings = 0
    for domain in domains:
        ds = analytical_state.get(domain, {})
        options = ds.get("options", [])[:3]
        dx, _ = domain_positions[domain]
        n = len(options)
        max_findings = max(max_findings, n)
        for j, opt in enumerate(options):
            fx = dx  # centered in column
            fy = findings_start_y + j * finding_v_gap
            fid = f"finding_{domain}_{j}"
            finding_nodes.append((fid, opt.get("name", f"Option {j+1}")[:20], domain, fx, fy, opt))

    # Topics start below all findings
    topics_start_y = findings_start_y + max_findings * finding_v_gap + 40

    # Group learning topics by linked domain; unlinked go to "general"
    domain_topics = {d: [] for d in domains}
    general_topics = []
    for tid in TOPIC_DAG:
        linked = TOPIC_DOMAIN_LINKS.get(tid)
        if linked and linked in domain_topics:
            domain_topics[linked].append(tid)
        else:
            general_topics.append(tid)

    # Position linked topics in their domain column (vertically stacked)
    topic_positions = {}
    for domain in domains:
        dx, _ = domain_positions[domain]
        tids = domain_topics[domain]
        for j, tid in enumerate(tids):
            cx = dx
            cy = topics_start_y + j * v_gap_topic
            topic_positions[tid] = (cx, cy)

    # Position general topics in rows below all columns
    max_linked_count = max(len(v) for v in domain_topics.values()) if domain_topics else 0
    general_start_y = topics_start_y + max_linked_count * v_gap_topic + 80
    cols_for_general = 4  # fewer columns = more space
    general_h_gap = (svg_width - 2 * col_margin) / cols_for_general
    for j, tid in enumerate(general_topics):
        row = j // cols_for_general
        col = j % cols_for_general
        cx = col_margin + general_h_gap / 2 + col * general_h_gap
        cy = general_start_y + row * v_gap_topic
        topic_positions[tid] = (cx, cy)

    general_rows = (len(general_topics) + cols_for_general - 1) // cols_for_general
    svg_height = general_start_y + general_rows * v_gap_topic + 80

    # ── Build SVG ──
    arrows_svg = ""
    nodes_svg = ""

    # Column background shading for visual grouping
    col_bg_height = topics_start_y + max_linked_count * v_gap_topic + 20
    for i, domain in enumerate(domains):
        x = col_margin + i * col_width
        color = DOMAIN_COLORS[domain]
        nodes_svg += (
            f'<rect x="{x}" y="30" width="{col_width - 10}" height="{col_bg_height}" '
            f'rx="16" fill="{color}" fill-opacity="0.03" stroke="{color}" stroke-width="0.5" stroke-opacity="0.15"/>\n'
        )

    # Edges: domain → findings (solid, bright)
    for fid, flabel, fdomain, fx, fy, fdata in finding_nodes:
        dx, dy = domain_positions[fdomain]
        color = DOMAIN_COLORS[fdomain]
        arrows_svg += (
            f'<line x1="{dx:.0f}" y1="{dy+28:.0f}" x2="{fx:.0f}" y2="{fy-16:.0f}" '
            f'stroke="{color}" stroke-width="2" opacity="0.7"/>\n'
        )

    # Edges: domain → linked topics (clear colored lines)
    for domain in domains:
        dx, dy = domain_positions[domain]
        color = DOMAIN_COLORS[domain]
        tids = domain_topics[domain]
        for tid in tids:
            if tid not in topic_positions:
                continue
            tx, ty = topic_positions[tid]
            arrows_svg += (
                f'<line x1="{dx:.0f}" y1="{dy+28:.0f}" x2="{tx:.0f}" y2="{ty - node_h/2:.0f}" '
                f'stroke="{color}" stroke-width="1.5" opacity="0.45" stroke-dasharray="6 3"/>\n'
            )

    # Edges: learning topic prereq arrows (uniform color)
    for tid, tdata in TOPIC_DAG.items():
        if tid not in topic_positions:
            continue
        tx, ty = topic_positions[tid]
        for prereq in tdata.get("prereqs", []):
            if prereq not in topic_positions:
                continue
            px, py = topic_positions[prereq]
            linked_domain = TOPIC_DOMAIN_LINKS.get(tid)
            edge_color = DOMAIN_COLORS.get(linked_domain, "#6b7b8d") if linked_domain else "#6b7b8d"
            mid_y = (py + node_h/2 + ty - node_h/2) / 2
            arrows_svg += (
                f'<path d="M{px:.0f},{py + node_h/2:.0f} C{px:.0f},{mid_y:.0f} {tx:.0f},{mid_y:.0f} {tx:.0f},{ty - node_h/2:.0f}" '
                f'fill="none" stroke="{edge_color}" stroke-width="1.5" opacity="0.5" '
                f'marker-end="url(#kg-arrow)"/>\n'
            )

    # Draw domain hub nodes (large, prominent)
    for domain, (dx, dy) in domain_positions.items():
        color = DOMAIN_COLORS[domain]
        icon = DOMAIN_ICONS[domain]
        ds = analytical_state.get(domain, {})
        conf = int(ds.get("confidence", 0) * 100)
        nodes_svg += (
            f'<g class="kg-node" data-type="domain" data-id="{domain}">'
            f'<rect x="{dx-70}" y="{dy-28}" width="140" height="56" rx="14" '
            f'fill="{color}" fill-opacity="0.25" stroke="{color}" stroke-width="3"/>'
            f'<text x="{dx}" y="{dy-4}" text-anchor="middle" font-size="14" fill="{color}" '
            f'font-weight="700" font-family="system-ui">{icon} {domain.title()}</text>'
            f'<text x="{dx}" y="{dy+18}" text-anchor="middle" font-size="11" fill="{color}" '
            f'opacity="0.9" font-family="system-ui">{conf}%</text>'
            f'</g>\n'
        )

    # Draw finding nodes (pills under domain)
    for fid, flabel, fdomain, fx, fy, fdata in finding_nodes:
        color = FINDING_COLOR
        pill_w = max(90, len(flabel) * 7 + 16)
        nodes_svg += (
            f'<g class="kg-node" data-type="finding" data-id="{fid}" data-domain="{fdomain}">'
            f'<rect x="{fx - pill_w/2:.0f}" y="{fy-14}" width="{pill_w}" height="28" rx="14" '
            f'fill="{color}" fill-opacity="0.2" stroke="{color}" stroke-width="1.8"/>'
            f'<text x="{fx}" y="{fy+4}" text-anchor="middle" font-size="9.5" fill="{color}" '
            f'font-family="system-ui" font-weight="600">{_esc(flabel)}</text>'
            f'</g>\n'
        )

    # Draw learning topic nodes — all uniform style, colored by linked domain
    TOPIC_COLOR = "#8b9dc3"  # neutral blue-gray for unlinked topics
    for tid in TOPIC_DAG:
        if tid not in topic_positions:
            continue
        cx, cy = topic_positions[tid]
        label = _esc(SHORT_LABELS.get(tid, tid[:14]))
        rx, ry = cx - node_w / 2, cy - node_h / 2
        font_size = "9.5" if len(label) > 14 else "10.5"
        linked_domain = TOPIC_DOMAIN_LINKS.get(tid)
        node_color = DOMAIN_COLORS.get(linked_domain, TOPIC_COLOR) if linked_domain else TOPIC_COLOR

        nodes_svg += (
            f'<g class="kg-node" data-type="topic" data-id="{tid}">'
            f'<rect x="{rx:.0f}" y="{ry:.0f}" width="{node_w}" height="{node_h}" '
            f'rx="8" fill="{node_color}" fill-opacity="0.18" '
            f'stroke="{node_color}" stroke-width="1.8"/>'
            f'<text x="{cx:.0f}" y="{cy + 4:.0f}" text-anchor="middle" '
            f'font-size="{font_size}" fill="#ddd" font-family="system-ui" '
            f'font-weight="500">{label}</text>'
            f'</g>\n'
        )

    # "General" section label
    layer_labels_svg = ""
    if general_topics:
        layer_labels_svg += (
            f'<text x="{col_margin}" y="{general_start_y - 30}" font-size="12" fill="#888" '
            f'font-family="system-ui" font-weight="600">General Topics (cross-domain)</text>\n'
            f'<line x1="{col_margin}" y1="{general_start_y - 20}" x2="{svg_width - col_margin}" y2="{general_start_y - 20}" '
            f'stroke="#444" stroke-width="0.5" stroke-dasharray="4 4"/>\n'
        )

    # ── Build node data JSON for side pane interactivity ──
    node_data = {}
    # Domain nodes
    for domain in domains:
        ds = analytical_state.get(domain, {})
        node_data[domain] = {
            "type": "domain", "title": f"{DOMAIN_ICONS[domain]} {domain.title()} Analysis",
            "analysis": ds.get("current_analysis", "No analysis yet.")[:800],
            "confidence": ds.get("confidence", 0),
            "options": [o.get("name", "") for o in ds.get("options", [])[:5]],
            "evidence_count": len(ds.get("evidence", [])),
            "link": f"pages/{domain}.html",
        }
    # Finding nodes
    for fid, flabel, fdomain, fx, fy, fdata in finding_nodes:
        node_data[fid] = {
            "type": "finding", "title": flabel, "domain": fdomain,
            "pros": fdata.get("pros", [])[:3],
            "cons": fdata.get("cons", [])[:3],
            "cost": fdata.get("cost", ""),
            "performance": fdata.get("performance", ""),
            "rank": fdata.get("recommendation_rank", 0),
        }
    # Topic nodes
    for tid, tinfo in TOPIC_DAG.items():
        ts = ks_topics.get(tid, {})
        topic_links = TOPIC_LINKS.get(tid, [])
        node_data[tid] = {
            "type": "topic", "title": tinfo["title"],
            "summary": TOPIC_SUMMARIES.get(tid, ""),
            "status": ts.get("status", "unseen"),
            "confidence": ts.get("confidence", 0),
            "key_facts": TOPIC_KEY_FACTS.get(tid, ts.get("key_facts", []))[:6],
            "links": [{"label": lbl, "url": url} for lbl, url in topic_links],
            "linked_domain": TOPIC_DOMAIN_LINKS.get(tid, ""),
            "lessons_count": len(ts.get("lessons", [])),
            "goal_tags": tinfo.get("goal_tags", []),
            "prereqs": [TOPIC_DAG[p]["title"] for p in tinfo.get("prereqs", []) if p in TOPIC_DAG],
        }

    node_data_json = json.dumps(node_data, ensure_ascii=False, default=str)

    # ── Assemble full SVG ──
    svg_html = f'''<div class="kg-container">
<svg viewBox="0 0 {svg_width} {svg_height}" xmlns="http://www.w3.org/2000/svg"
     class="kg-svg" preserveAspectRatio="xMidYMid meet">
  <defs>
    <marker id="kg-arrow" viewBox="0 0 10 10" refX="9" refY="5"
      markerWidth="5" markerHeight="5" orient="auto-start-reverse">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#888"/>
    </marker>
    <filter id="kg-glow"><feGaussianBlur stdDeviation="4" result="blur"/>
      <feFlood flood-color="#6366f1" flood-opacity="0.3"/>
      <feComposite in2="blur" operator="in"/>
      <feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>
    </filter>
  </defs>
  {layer_labels_svg}
  {arrows_svg}
  {nodes_svg}
</svg>
</div>
<!-- Side pane for node details -->
<div id="kg-side-pane" class="kg-side-pane">
  <button class="kg-pane-close" onclick="closeKgPane()">&times;</button>
  <div id="kg-pane-content"></div>
</div>
<div id="kg-overlay" class="kg-overlay" onclick="closeKgPane()"></div>
'''

    # ── CSS for knowledge graph ──
    kg_css = '''<style>
.kg-container { overflow: auto; border-radius: 12px; background: rgba(15,15,25,0.6);
  border: 1px solid rgba(255,255,255,0.06); padding: 12px; position: relative;
  width: 100%; height: calc(100vh - 140px); }
.kg-svg { width: 100%; height: 100%; display: block; }
.kg-node { cursor: pointer; pointer-events: bounding-box; }
.kg-node rect, .kg-node circle { cursor: pointer; pointer-events: all; transition: fill-opacity 0.2s, stroke-width 0.2s; }
.kg-node:hover rect { fill-opacity: 0.5 !important; stroke-width: 3 !important; }
.kg-node:hover text { font-weight: 700 !important; fill: #fff !important; }
.kg-side-pane { position: fixed; top: 0; right: -440px; width: 420px; height: 100vh;
  background: rgba(13,17,23,0.98); border-left: 2px solid var(--accent2);
  padding: 24px; overflow-y: auto; z-index: 1000; transition: right 0.3s ease;
  box-shadow: -6px 0 30px rgba(0,0,0,0.5); }
.kg-side-pane.open { right: 0; }
.kg-overlay { display: none; position: fixed; top: 0; left: 0; width: 100vw; height: 100vh;
  background: rgba(0,0,0,0.4); z-index: 999; }
.kg-overlay.open { display: block; }
.kg-pane-close { position: absolute; top: 12px; right: 16px; font-size: 1.6rem; color: var(--dim);
  background: none; border: none; cursor: pointer; padding: 4px 8px; border-radius: 4px; }
.kg-pane-close:hover { color: var(--text); background: rgba(99,102,241,0.2); }
.kg-pane-title { font-size: 1.1rem; font-weight: 700; margin-bottom: 12px; color: var(--text); }
.kg-pane-badge { display: inline-block; font-size: 0.72rem; padding: 2px 8px; border-radius: 10px;
  margin-right: 6px; margin-bottom: 8px; }
.kg-pane-section { margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border); }
.kg-pane-section h4 { font-size: 0.82rem; color: var(--dim); margin-bottom: 8px; text-transform: uppercase; letter-spacing: 0.5px; }
.kg-pane-list { list-style: none; padding: 0; }
.kg-pane-list li { font-size: 0.85rem; padding: 4px 0; color: var(--text); line-height: 1.5; }
.kg-pane-list li::before { content: "→ "; color: var(--accent); }
.kg-pane-analysis { font-size: 0.85rem; line-height: 1.7; color: var(--text); white-space: pre-wrap;
  max-height: 300px; overflow-y: auto; padding: 12px; background: rgba(255,255,255,0.02);
  border-radius: 8px; border: 1px solid var(--border); }
.kg-pane-link { display: inline-block; margin-top: 12px; padding: 8px 16px; border-radius: 8px;
  background: var(--accent); color: #000; text-decoration: none; font-size: 0.82rem; font-weight: 600; }
.kg-pane-link:hover { opacity: 0.85; }
.kg-legend { display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 16px; font-size: 0.8rem; align-items: center; }
.kg-legend-item { display: flex; align-items: center; gap: 6px; }
.kg-legend-dot { width: 10px; height: 10px; border-radius: 50%; }
@media (max-width: 700px) { .kg-side-pane { width: 100vw; right: -100vw; } .kg-side-pane.open { right: 0; } }
</style>'''

    # ── JS for interactivity ──
    kg_js = f'''<script>
document.addEventListener("DOMContentLoaded", function() {{
  var nodeData = {node_data_json};
  var pane = document.getElementById("kg-side-pane");
  var overlay = document.getElementById("kg-overlay");
  var content = document.getElementById("kg-pane-content");

  function esc(s) {{ var d=document.createElement("div"); d.textContent=s||""; return d.innerHTML; }}

  window.closeKgPane = function() {{
    pane.classList.remove("open");
    overlay.classList.remove("open");
  }};

  function renderDomain(d) {{
    var conf = Math.round((d.confidence||0)*100);
    var html = '<div class="kg-pane-title">' + esc(d.title) + '</div>';
    html += '<div class="kg-pane-badge" style="background:rgba(99,102,241,0.2);color:#a78bfa;">Domain Analysis</div>';
    html += '<div class="kg-pane-badge" style="background:rgba(59,130,246,0.15);color:#60a5fa;">' + conf + '% confidence</div>';
    html += '<div class="kg-pane-badge" style="background:rgba(16,185,129,0.15);color:#34d399;">' + (d.evidence_count||0) + ' evidence items</div>';
    html += '<div class="kg-pane-section"><h4>Current Analysis</h4><div class="kg-pane-analysis">' + esc(d.analysis) + '</div></div>';
    if (d.options && d.options.length) {{
      html += '<div class="kg-pane-section"><h4>Top Options</h4><ul class="kg-pane-list">';
      d.options.forEach(function(o) {{ html += '<li>' + esc(o) + '</li>'; }});
      html += '</ul></div>';
    }}
    if (d.link) html += '<a class="kg-pane-link" href="' + d.link + '">View Full Analysis →</a>';
    return html;
  }}

  function renderFinding(d) {{
    var html = '<div class="kg-pane-title">' + esc(d.title) + '</div>';
    html += '<div class="kg-pane-badge" style="background:rgba(244,114,182,0.2);color:#f472b6;">Research Finding</div>';
    if (d.rank) html += '<div class="kg-pane-badge" style="background:rgba(251,191,36,0.15);color:#fbbf24;">Rank #' + d.rank + '</div>';
    if (d.cost) html += '<div class="kg-pane-section"><h4>Cost</h4><p style="font-size:0.85rem;color:var(--text);">' + esc(d.cost) + '</p></div>';
    if (d.performance) html += '<div class="kg-pane-section"><h4>Performance</h4><p style="font-size:0.85rem;color:var(--text);">' + esc(d.performance) + '</p></div>';
    if (d.pros && d.pros.length) {{
      html += '<div class="kg-pane-section"><h4>Pros</h4><ul class="kg-pane-list">';
      d.pros.forEach(function(p) {{ html += '<li style="color:#34d399;">' + esc(p) + '</li>'; }});
      html += '</ul></div>';
    }}
    if (d.cons && d.cons.length) {{
      html += '<div class="kg-pane-section"><h4>Cons</h4><ul class="kg-pane-list">';
      d.cons.forEach(function(c) {{ html += '<li style="color:#f87171;">' + esc(c) + '</li>'; }});
      html += '</ul></div>';
    }}
    return html;
  }}

  function renderTopic(d) {{
    var html = '<div class="kg-pane-title">' + esc(d.title) + '</div>';
    if (d.linked_domain) html += '<div class="kg-pane-badge" style="background:rgba(244,114,182,0.1);color:#f472b6;">Domain: ' + d.linked_domain + '</div>';
    if (d.goal_tags && d.goal_tags.length) {{
      html += '<div style="margin-top:8px;">';
      d.goal_tags.forEach(function(g) {{ html += '<span class="kg-pane-badge" style="background:rgba(99,102,241,0.08);color:#a78bfa;font-size:0.68rem;">🎯 ' + esc(g.replace(/-/g," ")) + '</span>'; }});
      html += '</div>';
    }}
    if (d.summary) {{
      html += '<div class="kg-pane-section"><h4>Overview</h4><div class="kg-pane-analysis">' + esc(d.summary) + '</div></div>';
    }}
    if (d.key_facts && d.key_facts.length) {{
      html += '<div class="kg-pane-section"><h4>Key Facts</h4><ul class="kg-pane-list">';
      d.key_facts.forEach(function(f) {{ html += '<li>' + esc(f) + '</li>'; }});
      html += '</ul></div>';
    }}
    if (d.links && d.links.length) {{
      html += '<div class="kg-pane-section"><h4>📚 Read More</h4><ul class="kg-pane-list" style="list-style:none;padding-left:0;">';
      d.links.forEach(function(lk) {{ html += '<li style="margin-bottom:6px;"><a href="' + esc(lk.url) + '" target="_blank" rel="noopener" style="color:#60a5fa;text-decoration:none;font-size:0.82rem;">↗ ' + esc(lk.label) + '</a></li>'; }});
      html += '</ul></div>';
    }}
    if (d.prereqs && d.prereqs.length) {{
      html += '<div class="kg-pane-section"><h4>Prerequisites</h4><ul class="kg-pane-list">';
      d.prereqs.forEach(function(p) {{ html += '<li style="color:var(--dim);">' + esc(p) + '</li>'; }});
      html += '</ul></div>';
    }}
    html += '<div class="kg-pane-section"><h4>Discuss in CLI</h4>';
    html += '<code style="display:block;font-size:0.78rem;padding:8px 12px;background:rgba(0,0,0,0.3);border-radius:6px;color:var(--accent);word-break:break-all;">copilot --resume=\\"llm-monitor-learning\\" -p \\"Discuss: ' + esc(d.title) + '\\"</code></div>';
    return html;
  }}

  document.querySelectorAll(".kg-node").forEach(function(el) {{
    el.addEventListener("click", function() {{
      var id = el.getAttribute("data-id");
      var type = el.getAttribute("data-type");
      var d = nodeData[id];
      if (!d) return;
      var html = "";
      if (type === "domain") html = renderDomain(d);
      else if (type === "finding") html = renderFinding(d);
      else html = renderTopic(d);
      content.innerHTML = html;
      pane.classList.add("open");
      overlay.classList.add("open");
    }});
  }});
}});
</script>'''

    # ── Legend ──
    legend_html = (
        '<div class="kg-legend">'
        '<div class="kg-legend-item"><div class="kg-legend-dot" style="background:#3b82f6;"></div>Hardware</div>'
        '<div class="kg-legend-item"><div class="kg-legend-dot" style="background:#8b5cf6;"></div>Models</div>'
        '<div class="kg-legend-item"><div class="kg-legend-dot" style="background:#10b981;"></div>Optimization</div>'
        '<div class="kg-legend-item"><div class="kg-legend-dot" style="background:#f59e0b;"></div>Setup</div>'
        '<div class="kg-legend-item"><div class="kg-legend-dot" style="background:#f472b6;"></div>Research Findings</div>'
        '<div class="kg-legend-item"><div class="kg-legend-dot" style="background:#6366f1;"></div>Learning Topics</div>'
        '</div>'
    )

    body_content = (
        '\n<div class="header">'
        '\n  <div class="header-top">'
        '\n    <h1>🗺️ Knowledge Graph</h1>'
        f'\n    <div class="meta">Last updated: <b>{_esc(now)}</b></div>'
        '\n  </div>'
        '\n</div>'
        f'\n{kg_css}'
        '\n<div class="content">'
        '\n  <p style="color:var(--dim);margin-bottom:16px;font-size:0.88rem;">Unified map of research domains, findings, and learning topics. Click any node to explore details in the side pane.</p>'
        f'\n  {legend_html}'
        f'\n  {svg_html}'
        '\n</div>'
        f'\n{kg_js}'
    )

    modal_json = json.dumps({}, ensure_ascii=False, default=str)
    return _generate_page_shell("Knowledge Graph - LLM Homelab", nav_html, body_content, modal_json)


def generate_dashboard(state: dict, changes: list[dict], run_status: dict):
    """Generate multi-page HTML dashboard with analysis-first design."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Create pages directory
    os.makedirs(PAGES_DIR, exist_ok=True)

    # Generate Situation Room (index)
    main_html = _generate_situation_room(state, now)
    DASHBOARD_FILE.write_text(main_html, encoding="utf-8")
    INDEX_FILE.write_text(main_html, encoding="utf-8")

    # Generate Analysis Pages
    for domain in ("hardware", "models", "optimization", "setup"):
        page_html = _generate_analysis_page(state, domain, now)
        (PAGES_DIR / f"{domain}.html").write_text(page_html, encoding="utf-8")

    # Generate Knowledge Graph page
    kg_html = _generate_knowledge_graph_page(state, now)
    (PAGES_DIR / "knowledge.html").write_text(kg_html, encoding="utf-8")

    # Generate Ask page (enhanced existing)
    ask_html = _generate_ask_page(state, now)
    (PAGES_DIR / "ask.html").write_text(ask_html, encoding="utf-8")

    # Generate Timeline page (enhanced existing)
    timeline_html = _generate_timeline_page(state, now)
    (PAGES_DIR / "timeline.html").write_text(timeline_html, encoding="utf-8")

    logger.info(f"Dashboard updated: {DASHBOARD_FILE} + {PAGES_DIR}")


# ─── Main Execution ─────────────────────────────────────────────────────────

def main():
    logger.info("=" * 60)
    logger.info("LLM Homelab - Starting daily pipeline")
    logger.info("=" * 60)

    state = load_state()
    # Ensure knowledge state exists
    if "knowledge_state" not in state:
        state["knowledge_state"] = _init_knowledge_state()
        logger.info("Initialized fresh knowledge state")
    old_checks = state.get("checks", {})
    today = datetime.now().strftime("%B %d, %Y")

    # ── Run the multi-stage pipeline (parallel gathers) ──
    pipeline_result = run_pipeline(state)
    new_checks = pipeline_result["new_checks"]
    run_status = pipeline_result["run_status"]

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
            send_toast("✅ Monitor Started", "LLM Homelab is now active!", "info")

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
        "pattern": f"llm-homelab-{{category}}-{_RUN_DATE}-{_RUN_ID}",
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

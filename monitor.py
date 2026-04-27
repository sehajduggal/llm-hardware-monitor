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
from datetime import datetime, timedelta
from pathlib import Path
from logging.handlers import RotatingFileHandler

# ─── Configuration ───────────────────────────────────────────────────────────

MONITOR_DIR = Path(__file__).parent
STATE_FILE = MONITOR_DIR / "monitor_state.json"
LOG_FILE = MONITOR_DIR / "monitor.log"
DASHBOARD_FILE = MONITOR_DIR / "LLM-Hardware-Monitor.html"

# Use .cmd wrapper so subprocess can find it (not .ps1)
COPILOT_CMD = r"C:\ProgramData\global-npm\copilot.cmd"

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
        "Search the web and return ONLY a JSON object (no markdown fences, no explanation). "
        "Check each of these 7 items and return exactly this structure: "
        '{{"mac_studio_m5": {{"announced": false, "info": "summary"}}, '
        '"mac_studio_128gb_india": {{"in_stock": false, "orderable": false, "delivery_days": "unknown", "price_inr": "unknown", '
        '"info": "Check apple.com/in/shop/buy-mac/mac-studio for Mac Studio M4 Max 16-core CPU 40-core GPU 128GB unified memory config. Is it orderable or out of stock? What delivery estimate?"}}, '
        '"mac_studio_128gb_us": {{"in_stock": false, "orderable": false, "delivery_days": "unknown", "price_usd": "unknown", '
        '"info": "Check apple.com/shop/buy-mac/mac-studio for Mac Studio M4 Max 16-core CPU 40-core GPU 128GB unified memory config in US store. Is it orderable or out of stock?"}}, '
        '"apple_refurbished": {{"available": false, "info": "summary"}}, '
        '"wwdc_apple_event": {{"date": "TBD", "info": "summary"}}, '
        '"corsair_ws300_india": {{"available": false, "info": "summary"}}, '
        '"amd_strix_halo_128gb_india": {{"available": false, "info": "summary"}}}} '
        "For mac_studio_128gb_india and mac_studio_128gb_us: search specifically for the EXACT config (M4 Max, 16-core CPU, 40-core GPU, 128GB RAM). "
        "Report orderable=true only if add-to-bag works. Include delivery estimate and price. Return ONLY the JSON."
    ),

    "models_and_agents": (
        "You are an AI model monitoring agent. Today is {date}. "
        "Search the web and return ONLY a JSON object (no markdown fences, no explanation). "
        "Check each of these 4 items and return exactly this structure: "
        '{{"new_moe_models": {{"found": false, "info": "any MoE model better than Qwen3-30B-A3B for coding on Apple Silicon"}}, '
        '"new_coding_models": {{"found": false, "info": "new local coding models released in last 30 days"}}, '
        '"mlx_llama_cpp": {{"info": "latest MLX and llama.cpp updates for Apple Silicon"}}, '
        '"coding_agents": {{"info": "updates to OpenHands, Aider, SWE-agent, Copilot CLI, or new YOLO coding agent frameworks"}}}} '
        "Replace each info with real current findings. Return ONLY the JSON."
    ),

    "deals_and_blogs": (
        "You are a deals and tech news monitoring agent. Today is {date}. "
        "Search the web and return ONLY a JSON object (no markdown fences, no explanation). "
        "Check each of these 4 items and return exactly this structure: "
        '{{"apple_india_deals": {{"has_deals": false, "info": "Mac Studio deals, education discount, card offers on apple.com/in"}}, '
        '"mac_studio_marketplace": {{"info": "Mac Studio availability and prices on Amazon India, Flipkart"}}, '
        '"latest_local_llm_news": {{"info": "top 3 developments from r/LocalLLaMA, Hacker News about local LLM hardware in last 7 days"}}, '
        '"trending_models": {{"info": "top 3 trending models on HuggingFace relevant to local coding agents"}}}} '
        "Replace each info with real current findings. Return ONLY the JSON."
    ),
}

# ─── Enrichment Prompts (deeper analysis + links for modal) ──────────────────

ENRICHMENT_PROMPTS = {
    "models_deep": (
        "You are an AI researcher. Today is {date}. "
        "Do deep web searches for each item below. Return ONLY JSON with detailed analysis and source links. "
        '{{"new_moe_models": {{"analysis": "2-3 paragraphs on MoE models for local coding on Apple Silicon/AMD. Include benchmark scores, tok/s on 128GB unified memory, quantization options, comparison with Qwen3-30B-A3B", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"new_coding_models": {{"analysis": "2-3 paragraphs on new local coding models released recently. Benchmark scores on HumanEval/SWE-bench, parameter counts, best quantization for 128GB RAM", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"mlx_llama_cpp": {{"analysis": "Latest MLX and llama.cpp updates. Performance improvements, new model support, Apple Silicon optimizations, version numbers", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}, '
        '"coding_agents": {{"analysis": "Latest coding agent frameworks. OpenHands, Aider, SWE-agent, Copilot updates. New YOLO/autonomous coding capabilities, local LLM support", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "what_it_says"}}]}}}} '
        "1-3 REAL URLs per item. Replace all placeholders with real current data. ONLY JSON."
    ),
    "hardware_deals_links": (
        "You are a hardware researcher. Today is {date}. "
        "Search the web for each item. Return ONLY JSON with brief analysis and real source links: "
        '{{"mac_studio_m5": {{"analysis": "Latest M5 Mac Studio news, expected specs, release timeline", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}, '
        '"mac_studio_128gb_india": {{"analysis": "128GB Mac Studio availability in India, alternative purchase options", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}, '
        '"mac_studio_128gb_us": {{"analysis": "128GB Mac Studio US availability and pricing", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}, '
        '"apple_refurbished": {{"analysis": "Refurbished Mac Studio availability and pricing", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}, '
        '"amd_strix_halo_128gb_india": {{"analysis": "AMD Strix Halo 128GB options in India", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}, '
        '"apple_india_deals": {{"analysis": "Current Apple India deals and education offers", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}, '
        '"latest_local_llm_news": {{"analysis": "Top local LLM news from Reddit and HN", '
        '"links": [{{"url": "real_url", "title": "page_title", "desc": "brief"}}]}}}} '
        "1-3 REAL URLs per item. ONLY JSON."
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


def run_copilot(prompt: str, timeout: int = 180) -> str:
    """Run a Copilot CLI prompt and return the raw response text.
    
    Uses -s (silent) text mode. The response will contain tool progress
    indicators followed by the actual response. JSON extraction is handled
    by parse_json_response().
    """
    # .cmd files are invoked via cmd.exe which interprets shell metacharacters
    # even in list-mode subprocess. Escape them with ^ for safety.
    safe_prompt = prompt.replace("|", "^|").replace("&", "^&").replace("<", "^<").replace(">", "^>")
    cmd = [COPILOT_CMD, "-p", safe_prompt] + COPILOT_FLAGS
    logger.info(f"Running Copilot prompt ({len(prompt)} chars)...")

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
        logger.error("Copilot CLI not found! Is it installed and in PATH?")
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


def run_enrichment(old_enrichment: dict, today: str) -> dict:
    """Run deep-analysis prompts for richer modal content (analysis + links).
    
    Returns merged enrichment dict: {item_key: {analysis: str, links: [{url, title, desc}]}}.
    Falls back to cached data on failure.
    """
    enrichment = dict(old_enrichment)

    for prompt_key, prompt_template in ENRICHMENT_PROMPTS.items():
        prompt = prompt_template.format(date=today)
        logger.info(f"--- Enrichment: {prompt_key} ---")
        response = run_copilot(prompt, timeout=240)
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

    return enrichment


def build_recommendation_prompt(checks: dict, enrichment: dict, prev_recs: list, today: str) -> str:
    """Build a compact recommendation prompt (must stay under ~6000 chars for Windows cmd limit)."""
    # Compile concise market snapshot — key items only
    context_lines = []
    important_keys = {
        "mac_studio_m5", "mac_studio_128gb_india", "mac_studio_128gb_us",
        "amd_strix_halo_128gb_india", "apple_refurbished",
        "new_moe_models", "new_coding_models", "coding_agents",
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

    prev_text = ""
    if prev_recs:
        last = prev_recs[-1].get("data", {})
        prev_text = f" Prev: {last.get('recommendation','?')}-{last.get('best_option','?')[:40]}."

    return (
        f"You are a hardware advisor. {today}. "
        f"User: Budget INR 1.5-3.5L, India, needs 128GB unified memory, 24/7 coding agents, "
        f"30B+ models 25+ tok/s, Apple Silicon or AMD Strix Halo, buys from India/USA/Canada. "
        f"Wants fine-tuning too.{prev_text} "
        f"Market: {context} "
        "Search web for latest. Return ONLY JSON: "
        '{"recommendation": "buy_now or wait or consider_alternative", '
        '"best_option": "product name+config", '
        '"summary": "2-3 sentence rec", '
        '"reasoning": "2-3 paragraphs: why, price INR, availability, tok/s, alternatives, fine-tuning, wait?", '
        '"best_model": "best LLM for this HW", '
        '"model_config": "quant, ctx window, tok/s", '
        '"fine_tuning": "feasibility+approach", '
        '"cost_estimate_inr": "total INR", '
        '"buy_links": [{"url": "link", "title": "name", "desc": "brief"}], '
        '"wait_for": "what+timeline if wait", '
        '"confidence": "high or medium or low", '
        '"changed_since_last": "what changed or first_run"} '
        "Real data only. ONLY JSON."
    )


def run_recommendation(checks: dict, enrichment: dict, prev_recs: list, today: str) -> dict | None:
    """Generate daily setup recommendation based on all current data."""
    prompt = build_recommendation_prompt(checks, enrichment, prev_recs, today)
    logger.info(f"--- Daily Recommendation ({len(prompt)} chars) ---")
    response = run_copilot(prompt, timeout=300)
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

    if item in critical_items and new_value is True:
        return "critical"
    if item in important_items and new_value is True:
        return "important"
    return "info"


def send_toast(title: str, message: str, severity: str = "info"):
    """Send Windows toast notification via BurntToast PowerShell module.
    
    Features:
    - Click notification → opens dashboard HTML in default browser
    - App logo image for visual identity
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

    # Use BurntToast with click action to open dashboard, and app logo
    ps_cmd = f"""
    Import-Module BurntToast -ErrorAction SilentlyContinue

    $logoPath = '{logo_path}'
    $dashUri = '{dashboard_path}'

    $textBinding = New-BTText -Text '{safe_title}'
    $textBinding2 = New-BTText -Text '{safe_msg}'

    $btnOpen = New-BTButton -Content 'Open Dashboard' -Arguments $dashUri -ActivationType Protocol
    $actions = New-BTAction -Buttons $btnOpen

    $bindingParams = @{{
        Children = $textBinding, $textBinding2
    }}

    if (Test-Path $logoPath) {{
        $img = New-BTImage -Source $logoPath -AppLogoOverride -Crop Circle
        $bindingParams['AppLogo'] = $img
    }}

    $binding = New-BTBinding @bindingParams
    $visual = New-BTVisual -BindingGeneric $binding

    $content = New-BTContent -Visual $visual -Actions $actions -Launch $dashUri -ActivationType Protocol

    Submit-BTNotification -Content $content -UniqueIdentifier 'llm-monitor'
    """

    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True, timeout=15
        )
        logger.info(f"Toast sent: {title} - {message}")
    except Exception as e:
        logger.warning(f"Toast notification failed: {e}")


def generate_dashboard(state: dict, changes: list[dict], run_status: dict):
    """Generate timeline-based HTML dashboard with search, cards, and detail side-pane modal."""
    import html as html_lib
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    timeline = state.get("timeline", [])
    checks = state.get("checks", {})
    enrichment = state.get("enrichment", {})

    def esc(val):
        if val is None:
            return ""
        return html_lib.escape(str(val))

    cat_icons = {
        "hardware": "🖥️",
        "models_and_agents": "🧠",
        "deals_and_blogs": "💰",
    }
    cat_labels = {
        "hardware": "Hardware",
        "models_and_agents": "Models & Agents",
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

    # ── Build modal data (combined basic + enrichment) for JS embedding ──
    modal_data = {}
    for cat_key, cat_data in checks.items():
        if not isinstance(cat_data, dict):
            continue
        for item_key, item_val in cat_data.items():
            entry = {
                "label": item_key.replace("_", " ").title(),
                "category": cat_key,
                "categoryLabel": cat_labels.get(cat_key, cat_key),
                "icon": cat_icons.get(cat_key, "📦"),
                "info": "",
                "flags": {},
                "analysis": "",
                "links": [],
            }
            if isinstance(item_val, dict):
                entry["info"] = item_val.get("info", "")
                entry["flags"] = {k: v for k, v in item_val.items() if k != "info"}
            else:
                entry["info"] = str(item_val)

            # Merge enrichment data
            enr = enrichment.get(item_key, {})
            if isinstance(enr, dict):
                entry["analysis"] = enr.get("analysis", "")
                enr_links = enr.get("links", [])
                if isinstance(enr_links, list):
                    entry["links"] = enr_links

            # Fallback: add default link if no enrichment links
            if not entry["links"]:
                default_link = link_map.get(item_key, "")
                if default_link:
                    entry["links"] = [{"url": default_link, "title": entry["label"], "desc": "Default tracking link"}]

            modal_data[item_key] = entry

    # ── Build status bar ──
    status_items = []
    for cat_key, cat_data in checks.items():
        if not isinstance(cat_data, dict):
            continue
        for item_key, item_val in cat_data.items():
            if isinstance(item_val, dict):
                for bk in ("announced", "in_stock", "available", "found", "has_deals"):
                    if bk in item_val:
                        label = item_key.replace("_", " ").title()
                        val = item_val[bk]
                        color = "var(--green)" if val else "var(--red)"
                        status_items.append(f'<span class="status-pill" style="border-color:{color}"><span class="dot" style="background:{color}"></span>{esc(label)}</span>')
    status_bar = " ".join(status_items) if status_items else '<span class="dim">No data yet</span>'

    # ── Build timeline HTML ──
    timeline_html = ""
    for entry in reversed(timeline):
        ts = entry.get("timestamp", "")
        date_label = entry.get("date_label", ts)
        items = entry.get("items", [])
        if not items:
            continue

        cards_html = ""
        for item in items:
            key = item.get("key", "")
            label = item.get("label", key)
            data = item.get("data", {})
            severity = item.get("severity", "info")
            is_new = item.get("is_new", False)
            cat = item.get("category", "other")
            icon = cat_icons.get(cat, "📦")
            cat_label = cat_labels.get(cat, cat.replace("_", " ").title())

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
            elif isinstance(data, str):
                info = data

            sev_class = f"sev-{severity}"
            new_badge = '<span class="badge new">NEW</span>' if is_new else '<span class="badge update">UPDATED</span>'

            # Check if enrichment has data for richer indicator
            has_analysis = bool(enrichment.get(key, {}).get("analysis", ""))
            detail_indicator = '<span class="detail-hint">📖 Click for details</span>' if has_analysis else '<span class="detail-hint">↗ Click for info</span>'

            cards_html += f'''
            <div class="timeline-card {sev_class} clickable" onclick="openModal('{esc(key)}')"
                 data-search="{esc(label)} {esc(info)} {esc(cat_label)}" data-item="{esc(key)}">
              <div class="card-top">
                <span class="card-icon">{icon}</span>
                <span class="card-cat">{esc(cat_label)}</span>
                {new_badge}
              </div>
              <div class="card-title">{esc(label)}</div>
              {f'<div class="card-flags">{flags_html}</div>' if flags_html else ''}
              <div class="card-info">{esc(info[:150])}{"…" if len(info) > 150 else ""}</div>
              {detail_indicator}
            </div>'''

        timeline_html += f'''
        <div class="timeline-group" data-date="{esc(ts)}">
          <div class="timeline-date">
            <span class="date-dot"></span>
            <span class="date-text">{esc(date_label)}</span>
            <span class="date-time">{esc(ts)}</span>
            <span class="date-count">{len(items)} update{"s" if len(items)!=1 else ""}</span>
          </div>
          <div class="timeline-cards">{cards_html}</div>
        </div>'''

    if not timeline_html:
        timeline_html = '<div class="empty-state">No updates yet. First check will populate this timeline.</div>'

    # ── Run status badges ──
    run_bar = ""
    for cat, st in run_status.items():
        ico = "✅" if st == "success" else "❌"
        run_bar += f'<span class="run-badge {st}">{ico} {cat.replace("_"," ")}</span> '

    # ── Build recommendation hero card ──
    rec = state.get("recommendation", {})
    rec_html = ""
    if rec:
        rec_action = rec.get("recommendation", "wait")
        rec_best = rec.get("best_option", "")
        rec_summary = rec.get("summary", "")
        rec_model = rec.get("best_model", "")
        rec_cost = rec.get("cost_estimate_inr", "")
        rec_confidence = rec.get("confidence", "medium")
        rec_wait = rec.get("wait_for", "")

        rec_html = f'''
    <div class="rec-card" onclick="openRecModal()">
      <div class="rec-top">
        <span style="font-size:1.4em">🎯</span>
        <span style="font-weight:700;font-size:0.85em;color:var(--dim);text-transform:uppercase;letter-spacing:1px">Today's Recommendation</span>
        <span class="rec-badge {esc(rec_action)}">{esc(rec_action.replace("_"," "))}</span>
        <span class="rec-confidence {esc(rec_confidence)}">{esc(rec_confidence)} confidence</span>
      </div>
      <div class="rec-title">{esc(rec_best)}</div>
      <div class="rec-summary">{esc(rec_summary)}</div>
      <div class="rec-meta">
        <span class="rec-meta-item">🧠 <b>{esc(rec_model)}</b></span>
        <span class="rec-meta-item">💰 <b>{esc(rec_cost)}</b></span>
        {f'<span class="rec-meta-item">⏳ <b>{esc(rec_wait[:80])}</b></span>' if rec_wait else ''}
      </div>
      <span class="rec-hint">📖 Click for full analysis, model config, fine-tuning guide & buy links</span>
    </div>'''

    # Add recommendation to modal data under special key
    if rec:
        modal_data["__recommendation__"] = {
            "label": rec.get("best_option", "Daily Recommendation"),
            "category": "recommendation",
            "categoryLabel": "Daily Recommendation",
            "icon": "🎯",
            "info": rec.get("summary", ""),
            "flags": {
                "recommendation": rec.get("recommendation", ""),
                "confidence": rec.get("confidence", ""),
            },
            "analysis": rec.get("reasoning", ""),
            "links": rec.get("buy_links", []),
            # Extra recommendation fields for the modal
            "best_model": rec.get("best_model", ""),
            "model_config": rec.get("model_config", ""),
            "fine_tuning": rec.get("fine_tuning", ""),
            "wait_for": rec.get("wait_for", ""),
            "changed_since_last": rec.get("changed_since_last", ""),
            "cost_estimate_inr": rec.get("cost_estimate_inr", ""),
        }

    # ── Serialize modal data for JS ──
    modal_json = json.dumps(modal_data, ensure_ascii=False, default=str)

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LLM Hardware Monitor</title>
<style>
  :root {{
    --bg: #0a0e14; --surface: #131920; --card: #1a2029; --border: #262f3d;
    --text: #e6edf3; --dim: #6b7b8d; --accent: #58a6ff; --accent2: #a371f7;
    --green: #3fb950; --red: #f85149; --yellow: #d29922; --orange: #db6d28;
    --radius: 12px; --pane-bg: #0d1117;
  }}
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    font-family: 'SF Pro Display', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg); color: var(--text); min-height: 100vh; line-height: 1.6;
  }}

  /* ── Header ── */
  .header {{
    position: sticky; top: 0; z-index: 100;
    background: rgba(10,14,20,0.92); backdrop-filter: blur(20px);
    border-bottom: 1px solid var(--border); padding: 16px 24px;
  }}
  .header-top {{ display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; }}
  .header h1 {{
    font-size: 1.4em; font-weight: 700;
    background: linear-gradient(135deg, #58a6ff 0%, #a371f7 50%, #f778ba 100%);
    -webkit-background-clip: text; -webkit-text-fill-color: transparent;
  }}
  .header .meta {{ font-size: 0.8em; color: var(--dim); }}
  .header .meta b {{ color: var(--accent); font-weight: 500; }}

  /* ── Search ── */
  .search-wrap {{
    padding: 12px 24px; background: var(--surface); border-bottom: 1px solid var(--border);
    position: sticky; top: 65px; z-index: 99; display: flex; flex-direction: column;
  }}
  .search-row {{ position: relative; }}
  .search-box {{
    width: 100%; max-width: 600px; padding: 10px 16px 10px 40px;
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    color: var(--text); font-size: 0.95em; outline: none; transition: border-color 0.2s;
  }}
  .search-box:focus {{ border-color: var(--accent); }}
  .search-box::placeholder {{ color: var(--dim); }}
  .search-row .icon {{ position: absolute; left: 14px; top: 50%; transform: translateY(-50%); color: var(--dim); pointer-events: none; }}
  .filter-bar {{ display: flex; gap: 8px; margin-top: 10px; flex-wrap: wrap; }}
  .filter-btn {{
    padding: 5px 14px; border-radius: 20px; font-size: 0.8em;
    background: var(--card); border: 1px solid var(--border); color: var(--dim);
    cursor: pointer; transition: all 0.2s;
  }}
  .filter-btn:hover, .filter-btn.active {{
    background: rgba(88,166,255,0.15); color: var(--accent); border-color: var(--accent);
  }}

  /* ── Status Bar ── */
  .status-bar {{
    display: flex; flex-wrap: wrap; gap: 8px; padding: 16px 24px;
    background: var(--surface); border-bottom: 1px solid var(--border); overflow-x: auto;
  }}
  .status-pill {{
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 20px; font-size: 0.78em;
    background: var(--card); border: 1px solid var(--border); white-space: nowrap;
  }}
  .dot {{ width: 8px; height: 8px; border-radius: 50%; display: inline-block; flex-shrink: 0; }}

  /* ── Content ── */
  .content {{ max-width: 1100px; margin: 0 auto; padding: 24px; }}
  .run-status {{ display: flex; gap: 8px; margin-bottom: 20px; flex-wrap: wrap; }}
  .run-badge {{ padding: 4px 12px; border-radius: 12px; font-size: 0.78em; }}
  .run-badge.success {{ background: rgba(63,185,80,0.12); color: var(--green); }}
  .run-badge.error {{ background: rgba(248,81,73,0.12); color: var(--red); }}

  /* ── Timeline ── */
  .timeline {{ position: relative; }}
  .timeline::before {{
    content: ''; position: absolute; left: 18px; top: 0; bottom: 0;
    width: 2px; background: linear-gradient(180deg, var(--accent), var(--accent2), var(--border));
  }}
  .timeline-group {{ margin-bottom: 32px; position: relative; }}
  .timeline-date {{
    display: flex; align-items: center; gap: 12px;
    padding: 8px 0; margin-left: 40px; margin-bottom: 12px;
  }}
  .date-dot {{
    position: absolute; left: 12px; width: 14px; height: 14px;
    border-radius: 50%; background: var(--accent); border: 3px solid var(--bg);
    box-shadow: 0 0 0 2px var(--accent);
  }}
  .date-text {{ font-weight: 700; font-size: 1.1em; }}
  .date-time {{ color: var(--dim); font-size: 0.8em; }}
  .date-count {{
    padding: 2px 10px; border-radius: 12px; font-size: 0.75em;
    background: rgba(88,166,255,0.12); color: var(--accent);
  }}

  /* ── Cards ── */
  .timeline-cards {{
    display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
    gap: 12px; margin-left: 40px;
  }}
  .timeline-card {{
    background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 16px; transition: all 0.25s ease;
    border-left: 3px solid var(--border); position: relative; overflow: hidden;
    cursor: pointer;
  }}
  .timeline-card:hover {{
    transform: translateY(-2px); box-shadow: 0 8px 32px rgba(0,0,0,0.35);
    border-color: var(--accent);
  }}
  .timeline-card::after {{
    content: '→'; position: absolute; top: 12px; right: 14px;
    color: var(--dim); font-size: 1.1em; transition: all 0.2s;
  }}
  .timeline-card:hover::after {{ color: var(--accent); transform: translateX(3px); }}
  .timeline-card.sev-critical {{
    border-left-color: var(--red);
    background: linear-gradient(135deg, rgba(248,81,73,0.06), var(--card));
    animation: glow-red 3s ease-in-out infinite;
  }}
  .timeline-card.sev-important {{
    border-left-color: var(--yellow);
    background: linear-gradient(135deg, rgba(210,153,34,0.06), var(--card));
  }}
  @keyframes glow-red {{
    0%, 100% {{ box-shadow: 0 0 0 rgba(248,81,73,0); }}
    50% {{ box-shadow: 0 0 20px rgba(248,81,73,0.15); }}
  }}
  .card-top {{ display: flex; align-items: center; gap: 8px; margin-bottom: 8px; }}
  .card-icon {{ font-size: 1.1em; }}
  .card-cat {{ font-size: 0.75em; color: var(--dim); text-transform: uppercase; letter-spacing: 0.5px; }}
  .badge {{
    padding: 2px 8px; border-radius: 10px; font-size: 0.65em; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.5px;
  }}
  .badge.new {{ background: rgba(63,185,80,0.2); color: var(--green); }}
  .badge.update {{ background: rgba(88,166,255,0.2); color: var(--accent); }}
  .card-title {{ font-weight: 700; font-size: 1.05em; margin-bottom: 6px; }}
  .card-flags {{ display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; }}
  .flag {{
    display: inline-flex; align-items: center; gap: 4px;
    font-size: 0.78em; font-weight: 500;
  }}
  .card-info {{
    font-size: 0.85em; color: var(--dim); line-height: 1.5;
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical; overflow: hidden;
  }}
  .detail-hint {{
    display: block; margin-top: 8px; font-size: 0.75em; color: var(--accent);
    opacity: 0.7; transition: opacity 0.2s;
  }}
  .timeline-card:hover .detail-hint {{ opacity: 1; }}

  /* ── Modal (Side Pane) ── */
  .modal-overlay {{
    position: fixed; inset: 0; z-index: 1000;
    background: rgba(0,0,0,0.55); backdrop-filter: blur(6px);
    opacity: 0; visibility: hidden; transition: all 0.3s ease;
  }}
  .modal-overlay.open {{ opacity: 1; visibility: visible; }}
  .modal-pane {{
    position: absolute; right: 0; top: 0; bottom: 0;
    width: 560px; max-width: 92vw;
    background: var(--pane-bg); border-left: 1px solid var(--border);
    overflow-y: auto; overflow-x: hidden;
    transform: translateX(100%); transition: transform 0.35s cubic-bezier(0.16, 1, 0.3, 1);
    box-shadow: -12px 0 48px rgba(0,0,0,0.5);
  }}
  .modal-overlay.open .modal-pane {{ transform: translateX(0); }}
  .modal-close {{
    position: sticky; top: 0; float: right; z-index: 10;
    width: 40px; height: 40px; margin: 12px 12px 0 0;
    background: var(--card); border: 1px solid var(--border); border-radius: 10px;
    color: var(--dim); font-size: 1.2em; cursor: pointer;
    display: flex; align-items: center; justify-content: center; transition: all 0.2s;
  }}
  .modal-close:hover {{ color: var(--text); background: var(--surface); }}
  .modal-body {{ padding: 20px 28px 40px; }}
  .modal-header {{
    display: flex; align-items: flex-start; gap: 14px;
    margin-bottom: 20px; padding-bottom: 16px; border-bottom: 1px solid var(--border);
  }}
  .modal-icon {{ font-size: 2em; line-height: 1; }}
  .modal-title {{ font-size: 1.3em; font-weight: 700; margin-bottom: 4px; }}
  .modal-cat {{
    font-size: 0.8em; color: var(--accent); text-transform: uppercase;
    letter-spacing: 0.5px; font-weight: 500;
  }}
  .modal-flags {{
    display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 20px;
  }}
  .modal-flag {{
    display: inline-flex; align-items: center; gap: 6px;
    padding: 6px 14px; border-radius: 8px; font-size: 0.85em; font-weight: 600;
    background: var(--card); border: 1px solid var(--border);
  }}
  .modal-section {{ margin-bottom: 24px; }}
  .modal-section h3 {{
    font-size: 0.85em; text-transform: uppercase; letter-spacing: 0.8px;
    color: var(--accent); margin-bottom: 10px; font-weight: 600;
    display: flex; align-items: center; gap: 8px;
  }}
  .modal-section h3::before {{
    content: ''; display: inline-block; width: 3px; height: 14px;
    background: var(--accent); border-radius: 2px;
  }}
  .modal-summary {{
    font-size: 0.95em; line-height: 1.7; color: var(--text);
    background: var(--surface); padding: 14px 18px; border-radius: 10px;
    border: 1px solid var(--border);
  }}
  .modal-analysis {{
    font-size: 0.92em; line-height: 1.8; color: #c9d1d9;
    background: var(--surface); padding: 16px 20px; border-radius: 10px;
    border: 1px solid var(--border); white-space: pre-wrap;
  }}
  .modal-analysis:empty {{ display: none; }}

  /* ── Links Table ── */
  .links-table {{
    width: 100%; border-collapse: collapse; font-size: 0.88em;
    background: var(--surface); border-radius: 10px; overflow: hidden;
    border: 1px solid var(--border);
  }}
  .links-table th {{
    text-align: left; padding: 10px 14px; font-size: 0.78em;
    text-transform: uppercase; letter-spacing: 0.5px; color: var(--dim);
    background: var(--card); border-bottom: 1px solid var(--border); font-weight: 600;
  }}
  .links-table td {{
    padding: 10px 14px; border-bottom: 1px solid var(--border);
    vertical-align: top;
  }}
  .links-table tr:last-child td {{ border-bottom: none; }}
  .links-table tr:hover td {{ background: rgba(88,166,255,0.04); }}
  .links-table a {{
    color: var(--accent); text-decoration: none; font-weight: 500;
    display: inline-flex; align-items: center; gap: 4px;
  }}
  .links-table a:hover {{ text-decoration: underline; }}
  .links-table a::after {{ content: '↗'; font-size: 0.8em; opacity: 0.6; }}
  .links-table .link-desc {{ color: var(--dim); font-size: 0.9em; margin-top: 2px; }}
  .no-links {{ color: var(--dim); font-style: italic; font-size: 0.9em; padding: 12px; }}

  .empty-state {{ text-align: center; padding: 60px 20px; color: var(--dim); font-size: 1.1em; }}
  .hidden {{ display: none !important; }}

  /* ── Recommendation Hero Card ── */
  .rec-card {{
    background: linear-gradient(135deg, rgba(88,166,255,0.08) 0%, rgba(163,113,247,0.08) 50%, rgba(247,120,186,0.06) 100%);
    border: 1px solid rgba(88,166,255,0.25); border-radius: 16px;
    padding: 24px 28px; margin-bottom: 28px; position: relative; overflow: hidden; cursor: pointer;
    transition: all 0.3s ease;
  }}
  .rec-card:hover {{
    border-color: var(--accent); box-shadow: 0 8px 40px rgba(88,166,255,0.12);
    transform: translateY(-2px);
  }}
  .rec-card::before {{
    content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
    background: linear-gradient(90deg, var(--accent), var(--accent2), #f778ba);
  }}
  .rec-card::after {{
    content: '→'; position: absolute; top: 20px; right: 20px;
    color: var(--dim); font-size: 1.3em; transition: all 0.2s;
  }}
  .rec-card:hover::after {{ color: var(--accent); transform: translateX(4px); }}
  .rec-top {{ display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }}
  .rec-badge {{
    padding: 4px 12px; border-radius: 20px; font-size: 0.72em; font-weight: 700;
    text-transform: uppercase; letter-spacing: 0.8px;
  }}
  .rec-badge.buy_now {{ background: rgba(63,185,80,0.2); color: var(--green); }}
  .rec-badge.wait {{ background: rgba(210,153,34,0.2); color: var(--yellow); }}
  .rec-badge.consider_alternative {{ background: rgba(88,166,255,0.2); color: var(--accent); }}
  .rec-title {{ font-size: 1.2em; font-weight: 700; margin-bottom: 6px; }}
  .rec-summary {{ font-size: 0.95em; color: #c9d1d9; line-height: 1.7; margin-bottom: 14px; }}
  .rec-meta {{
    display: flex; flex-wrap: wrap; gap: 16px; font-size: 0.82em; color: var(--dim);
  }}
  .rec-meta-item {{ display: flex; align-items: center; gap: 6px; }}
  .rec-meta-item b {{ color: var(--text); font-weight: 600; }}
  .rec-hint {{
    display: block; margin-top: 12px; font-size: 0.75em; color: var(--accent); opacity: 0.7;
  }}

  /* ── Recommendation Modal extras ── */
  .rec-section {{ margin-bottom: 20px; }}
  .rec-section h4 {{
    font-size: 0.82em; text-transform: uppercase; letter-spacing: 0.6px;
    color: var(--accent2); margin-bottom: 8px; font-weight: 600;
  }}
  .rec-section-body {{
    font-size: 0.92em; line-height: 1.8; color: #c9d1d9;
    background: var(--surface); padding: 14px 18px; border-radius: 10px;
    border: 1px solid var(--border); white-space: pre-wrap;
  }}
  .rec-confidence {{
    display: inline-flex; align-items: center; gap: 6px;
    padding: 4px 12px; border-radius: 12px; font-size: 0.78em; font-weight: 600;
  }}
  .rec-confidence.high {{ background: rgba(63,185,80,0.15); color: var(--green); }}
  .rec-confidence.medium {{ background: rgba(210,153,34,0.15); color: var(--yellow); }}
  .rec-confidence.low {{ background: rgba(248,81,73,0.15); color: var(--red); }}

  /* ── Footer ── */
  .footer {{
    text-align: center; padding: 30px; color: var(--dim); font-size: 0.78em;
    border-top: 1px solid var(--border); margin-top: 40px;
  }}
  .footer a {{ color: var(--accent); text-decoration: none; }}

  @media (max-width: 600px) {{
    .timeline-cards {{ grid-template-columns: 1fr; }}
    .header h1 {{ font-size: 1.1em; }}
    .content {{ padding: 16px; }}
    .modal-pane {{ width: 100vw; max-width: 100vw; }}
    .modal-body {{ padding: 16px; }}
  }}
</style>
</head>
<body>

<div class="header">
  <div class="header-top">
    <h1>🖥️ LLM Hardware Monitor</h1>
    <div class="meta">Last check: <b>{now}</b> · {len(timeline)} runs tracked</div>
  </div>
</div>

<div class="search-wrap">
  <div class="search-row">
    <span class="icon">🔍</span>
    <input type="text" class="search-box" id="search" placeholder="Search updates... (e.g. Mac Studio, Qwen, WWDC, deals)" autocomplete="off">
  </div>
  <div class="filter-bar">
    <button class="filter-btn active" data-filter="all">All</button>
    <button class="filter-btn" data-filter="hardware">🖥️ Hardware</button>
    <button class="filter-btn" data-filter="models_and_agents">🧠 Models</button>
    <button class="filter-btn" data-filter="deals_and_blogs">💰 Deals</button>
    <button class="filter-btn" data-filter="critical">🚨 Critical Only</button>
  </div>
</div>

<div class="status-bar">{status_bar}</div>

<div class="content">
  <div class="run-status">{run_bar}</div>
  {rec_html}
  <div class="timeline" id="timeline">{timeline_html}</div>
</div>

<!-- Side Pane Modal -->
<div class="modal-overlay" id="modalOverlay" onclick="closeModal()">
  <div class="modal-pane" onclick="event.stopPropagation()">
    <button class="modal-close" onclick="closeModal()">✕</button>
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
    </div>
  </div>
</div>

<div class="footer">
  Powered by <a href="https://github.com/features/copilot">GitHub Copilot CLI</a> ·
  Checks daily at 9 AM via Windows Task Scheduler ·
  <a href="file:///{str(MONITOR_DIR).replace(chr(92), '/')}/monitor.log">View Log</a> ·
  <a href="file:///{str(MONITOR_DIR).replace(chr(92), '/')}/monitor_state.json">View State</a>
</div>

<script>
// ── Modal Data ──
const modalData = {modal_json};

// ── Search & Filter ──
const searchBox = document.getElementById('search');
const cards = document.querySelectorAll('.timeline-card');
const groups = document.querySelectorAll('.timeline-group');
let activeFilter = 'all';

searchBox.addEventListener('input', () => filterCards(searchBox.value.toLowerCase(), activeFilter));

document.querySelectorAll('.filter-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    activeFilter = btn.dataset.filter;
    filterCards(searchBox.value.toLowerCase(), activeFilter);
  }});
}});

function filterCards(query, filter) {{
  cards.forEach(card => {{
    const text = (card.dataset.search || '').toLowerCase();
    const matchesSearch = !query || text.includes(query);
    let matchesFilter = filter === 'all';
    if (filter === 'hardware') matchesFilter = text.includes('hardware');
    if (filter === 'models_and_agents') matchesFilter = text.includes('models');
    if (filter === 'deals_and_blogs') matchesFilter = text.includes('deals') || text.includes('news');
    if (filter === 'critical') matchesFilter = card.classList.contains('sev-critical') || card.classList.contains('sev-important');
    card.classList.toggle('hidden', !(matchesSearch && matchesFilter));
  }});
  groups.forEach(g => {{
    const vis = g.querySelectorAll('.timeline-card:not(.hidden)').length;
    g.classList.toggle('hidden', vis === 0);
  }});
}}

// ── Modal ──
const overlay = document.getElementById('modalOverlay');

function openModal(itemKey) {{
  const item = modalData[itemKey];
  if (!item) return;

  document.getElementById('modalIcon').textContent = item.icon;
  document.getElementById('modalTitle').textContent = item.label;
  document.getElementById('modalCat').textContent = item.categoryLabel;

  // Flags
  const flagsEl = document.getElementById('modalFlags');
  flagsEl.innerHTML = '';
  if (item.flags) {{
    for (const [k, v] of Object.entries(item.flags)) {{
      if (typeof v === 'boolean' || typeof v === 'string') {{
        const color = v === true ? 'var(--green)' : v === false ? 'var(--red)' : 'var(--dim)';
        const label = k.replace(/_/g, ' ').replace(/\\b\\w/g, c => c.toUpperCase());
        const val = typeof v === 'boolean' ? (v ? 'Yes ✓' : 'No ✗') : v;
        flagsEl.innerHTML += `<span class="modal-flag" style="border-color:${{color}}"><span class="dot" style="background:${{color}}"></span>${{label}}: ${{val}}</span>`;
      }}
    }}
  }}

  // Summary
  document.getElementById('modalSummary').textContent = item.info || 'No summary available.';

  // Analysis
  const analysisEl = document.getElementById('modalAnalysis');
  const analysisSec = document.getElementById('analysisSection');
  analysisSec.querySelector('h3').textContent = 'Detailed Analysis';
  if (item.analysis) {{
    analysisEl.innerHTML = '';
    analysisEl.textContent = item.analysis;
    analysisSec.style.display = '';
  }} else {{
    analysisSec.style.display = 'none';
  }}

  // Links table
  const tbody = document.getElementById('modalLinksBody');
  const table = document.getElementById('modalLinksTable');
  const noLinks = document.getElementById('noLinks');
  tbody.innerHTML = '';

  if (item.links && item.links.length > 0) {{
    table.style.display = '';
    noLinks.style.display = 'none';
    item.links.forEach(lnk => {{
      const url = lnk.url || '';
      const title = lnk.title || url.replace(/https?:\\/\\//, '').split('/')[0];
      const desc = lnk.desc || lnk.description || '';
      const row = document.createElement('tr');
      row.innerHTML = `<td><a href="${{url}}" target="_blank" rel="noopener">${{escH(title)}}</a></td><td><span class="link-desc">${{escH(desc)}}</span></td>`;
      tbody.appendChild(row);
    }});
  }} else {{
    table.style.display = 'none';
    noLinks.style.display = '';
  }}

  overlay.classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function closeModal() {{
  overlay.classList.remove('open');
  document.body.style.overflow = '';
}}

function openRecModal() {{
  const rec = modalData['__recommendation__'];
  if (!rec) return;

  document.getElementById('modalIcon').textContent = '🎯';
  document.getElementById('modalTitle').textContent = rec.label;
  document.getElementById('modalCat').textContent = 'Daily Recommendation';

  // Flags as styled badges
  const flagsEl = document.getElementById('modalFlags');
  flagsEl.innerHTML = '';
  const action = rec.flags?.recommendation || '';
  const conf = rec.flags?.confidence || 'medium';
  flagsEl.innerHTML = `<span class="rec-badge ${{action}}">${{action.replace(/_/g,' ')}}</span>` +
    `<span class="rec-confidence ${{conf}}">${{conf}} confidence</span>`;

  // Summary
  document.getElementById('modalSummary').textContent = rec.info || '';

  // Build rich analysis with extra sections
  const analysisEl = document.getElementById('modalAnalysis');
  const analysisSec = document.getElementById('analysisSection');
  let analysisHtml = '';

  if (rec.analysis) {{
    analysisHtml += `<div class="rec-section"><h4>💡 Reasoning</h4><div class="rec-section-body">${{escH(rec.analysis)}}</div></div>`;
  }}
  if (rec.best_model) {{
    analysisHtml += `<div class="rec-section"><h4>🧠 Best Model</h4><div class="rec-section-body"><b>${{escH(rec.best_model)}}</b>` +
      (rec.model_config ? `\\n${{escH(rec.model_config)}}` : '') + `</div></div>`;
  }}
  if (rec.fine_tuning) {{
    analysisHtml += `<div class="rec-section"><h4>🔧 Fine-Tuning</h4><div class="rec-section-body">${{escH(rec.fine_tuning)}}</div></div>`;
  }}
  if (rec.wait_for) {{
    analysisHtml += `<div class="rec-section"><h4>⏳ What to Wait For</h4><div class="rec-section-body">${{escH(rec.wait_for)}}</div></div>`;
  }}
  if (rec.cost_estimate_inr) {{
    analysisHtml += `<div class="rec-section"><h4>💰 Cost Estimate</h4><div class="rec-section-body">${{escH(rec.cost_estimate_inr)}}</div></div>`;
  }}
  if (rec.changed_since_last) {{
    analysisHtml += `<div class="rec-section"><h4>🔄 What Changed</h4><div class="rec-section-body">${{escH(rec.changed_since_last)}}</div></div>`;
  }}

  if (analysisHtml) {{
    analysisEl.innerHTML = analysisHtml;
    analysisSec.style.display = '';
    analysisSec.querySelector('h3').textContent = 'Full Analysis';
  }} else {{
    analysisSec.style.display = 'none';
  }}

  // Links table (buy links)
  const tbody = document.getElementById('modalLinksBody');
  const table = document.getElementById('modalLinksTable');
  const noLinks = document.getElementById('noLinks');
  tbody.innerHTML = '';

  if (rec.links && rec.links.length > 0) {{
    table.style.display = '';
    noLinks.style.display = 'none';
    rec.links.forEach(lnk => {{
      const url = lnk.url || '';
      const title = lnk.title || url.replace(/https?:\\/\\//, '').split('/')[0];
      const desc = lnk.desc || lnk.description || '';
      const row = document.createElement('tr');
      row.innerHTML = `<td><a href="${{url}}" target="_blank" rel="noopener">${{escH(title)}}</a></td><td><span class="link-desc">${{escH(desc)}}</span></td>`;
      tbody.appendChild(row);
    }});
  }} else {{
    table.style.display = 'none';
    noLinks.style.display = '';
  }}

  overlay.classList.add('open');
  document.body.style.overflow = 'hidden';
}}

function escH(s) {{
  const d = document.createElement('div');
  d.textContent = s || '';
  return d.innerHTML;
}}

// ── Keyboard shortcuts ──
document.addEventListener('keydown', (e) => {{
  if (e.key === 'Escape') {{
    if (overlay.classList.contains('open')) {{
      closeModal();
    }} else {{
      searchBox.value = '';
      searchBox.blur();
      filterCards('', activeFilter);
    }}
  }}
  if (e.key === '/' && document.activeElement !== searchBox && !overlay.classList.contains('open')) {{
    e.preventDefault();
    searchBox.focus();
  }}
}});
</script>
</body>
</html>'''

    DASHBOARD_FILE.write_text(html, encoding="utf-8")
    logger.info(f"Dashboard updated: {DASHBOARD_FILE}")


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
    for category, prompt_template in PROMPTS.items():
        logger.info(f"--- Checking: {category} ---")
        prompt = prompt_template.format(date=today)
        response = run_copilot(prompt)

        if not response:
            logger.error(f"Empty response for {category}")
            run_status[category] = "error"
            # Keep old data for this category
            if category in old_checks:
                new_checks[category] = old_checks[category]
            continue

        parsed = parse_json_response(response)
        if parsed:
            new_checks[category] = parsed
            run_status[category] = "success"
            logger.info(f"{category}: parsed successfully ({len(parsed)} items)")
        else:
            run_status[category] = "error"
            logger.error(f"{category}: failed to parse JSON")
            # Store raw response for debugging
            raw_file = MONITOR_DIR / f"raw_{category}_{datetime.now().strftime('%Y%m%d')}.txt"
            raw_file.write_text(response, encoding="utf-8")
            # Keep old data
            if category in old_checks:
                new_checks[category] = old_checks[category]

    # Detect changes
    changes = detect_changes(old_checks, new_checks) if old_checks else []

    # Run enrichment for richer modal content (analysis + links)
    old_enrichment = state.get("enrichment", {})
    enrichment = run_enrichment(old_enrichment, today)
    state["enrichment"] = enrichment

    # Generate daily recommendation based on all data + user constraints
    prev_recs = state.get("recommendations", [])
    rec_data = run_recommendation(new_checks, enrichment, prev_recs, today)
    if rec_data:
        state["recommendation"] = rec_data
        rec_entry = {"date": datetime.now().strftime("%Y-%m-%d"), "data": rec_data}
        if "recommendations" not in state:
            state["recommendations"] = []
        state["recommendations"].append(rec_entry)
        state["recommendations"] = state["recommendations"][-90:]
    else:
        logger.warning("Recommendation generation failed, keeping previous")

    # Send notificationsfor important changes
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
        else:
            logger.info("First run — establishing baseline")
            send_toast("✅ Monitor Started", "LLM Hardware Monitor is now active!", "info")

    # Update state
    state["last_run"] = datetime.now().isoformat()
    state["checks"] = new_checks

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
    save_state(state)

    # Generate dashboard
    generate_dashboard(state, changes, run_status)

    logger.info("Daily check complete!")
    return 0


if __name__ == "__main__":
    sys.exit(main())

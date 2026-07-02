#!/usr/bin/env python3
"""DeepSeek Token Monitor — pywebview floating window for Claude Code usage."""

import json
import os
import sys
import threading
import time
from datetime import date
from pathlib import Path

import webview

# ====================== config ======================
CLAUDE_DIR  = Path.home() / ".claude/projects/C--Users-admin"
REFRESH_SEC = 1
PRICE = {
    "cache_hit":  0.025,   # CNY per million tokens
    "cache_miss": 3.0,
    "output":     6.0,
}
# ====================================================

def get_balance():
    """Fetch DeepSeek account balance via API."""
    import urllib.request
    try:
        settings = Path.home() / ".claude/settings.json"
        with open(settings) as f:
            cfg = json.load(f)
        key = cfg.get("env", {}).get("ANTHROPIC_AUTH_TOKEN", "")
        if not key:
            return "N/A"
        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        for info in data.get("balance_infos", []):
            return f"{float(info['total_balance']):.2f} {info['currency']}"
        return "N/A"
    except Exception:
        return "N/A"

def parse_usage():
    """Sum token usage from today's Claude Code session JSONL files.

    One DeepSeek API response is recorded as multiple "assistant" entries
    (text block + tool call blocks), all sharing the same message.id.
    We deduplicate by message.id to avoid double-counting tokens.
    """
    today_str = date.today().strftime("%Y-%m-%d")
    if not CLAUDE_DIR.exists():
        return None

    seen_mids = set()  # dedup by message.id = one count per real API response
    stats = {
        "calls": 0,
        "cache_hit": 0,
        "cache_miss": 0,
        "cache_create": 0,
        "output": 0,
    }

    for path in CLAUDE_DIR.glob("*.jsonl"):
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    # Only today's entries (timestamp is UTC: "2026-07-02T09:45:24.293Z")
                    ts = d.get("timestamp", "")
                    if not ts.startswith(today_str):
                        continue

                    if d.get("type") != "assistant":
                        continue
                    usage = d.get("message", {}).get("usage", {})
                    if not usage:
                        continue

                    # message.id = DeepSeek API response id — the true dedup key
                    mid = d.get("message", {}).get("id", "")
                    if not mid or mid in seen_mids:
                        continue
                    seen_mids.add(mid)
                    stats["calls"] += 1

                    stats["cache_hit"]   += usage.get("cache_read_input_tokens", 0)
                    stats["cache_miss"]  += usage.get("input_tokens", 0)
                    stats["cache_create"] += usage.get("cache_creation_input_tokens", 0)
                    stats["output"]      += usage.get("output_tokens", 0)
        except Exception:
            continue

    return stats

def compute_display(stats):
    """Convert raw stats into display-ready values."""
    if stats is None or stats["calls"] == 0:
        return None

    s = stats
    prompt_total = s["cache_hit"] + s["cache_miss"] + s["cache_create"]
    grand_total  = prompt_total + s["output"]
    hit_rate = (s["cache_hit"] / prompt_total * 100) if prompt_total > 0 else 0
    cost = (
        s["cache_hit"]   / 1_000_000 * PRICE["cache_hit"] +
        (s["cache_miss"] + s["cache_create"]) / 1_000_000 * PRICE["cache_miss"] +
        s["output"]      / 1_000_000 * PRICE["output"]
    )
    return {
        "calls":        s["calls"],
        "cache_hit":    s["cache_hit"],
        "cache_miss":   s["cache_miss"],
        "cache_create": s["cache_create"],
        "output":       s["output"],
        "prompt_total": prompt_total,
        "grand_total":  grand_total,
        "hit_rate":     hit_rate,
        "cost":         cost,
    }

def fmt_tokens(n):
    """Format token count: 1234567 -> 1.23M"""
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)

# ====================== HTML UI ======================

HTML = """
<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<style>
  :root {
    --bg:    rgba(18, 22, 28, 0.92);
    --card:  rgba(255,255,255,0.04);
    --text:  #c8ccd4;
    --dim:   #7a7f8a;
    --green: #4ade80;
    --blue:  #60a5fa;
    --amber: #fbbf24;
    --purple:#c084fc;
    --red:   #f87171;
    --border:rgba(255,255,255,0.06);
  }

  * { margin:0; padding:0; box-sizing:border-box; }

  html { height: 100%; }
  body { height: 100%;
    background: var(--bg);
    color: var(--text);
    font-family: "Segoe UI", "Microsoft YaHei", sans-serif;
    font-size: 13px;
    -webkit-app-region: drag;
    user-select: none;
    overflow: hidden;
    border-radius: 12px;
    border: 1px solid rgba(255,255,255,0.08);
    backdrop-filter: blur(20px);
  }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 10px 2px;
  }

  .title {
    font-size: 11px;
    font-weight: 600;
    color: #e2e6ed;
    letter-spacing: 0.3px;
  }

  .title .dot {
    display: inline-block; width:5px; height:5px;
    border-radius:50%; background: var(--green);
    margin-right: 4px; vertical-align:1px;
    box-shadow: 0 0 6px var(--green);
  }

  .close-btn {
    -webkit-app-region: no-drag;
    width: 18px; height: 18px;
    border-radius: 6px; border: none;
    background: rgba(255,255,255,0.04);
    color: var(--dim); cursor: pointer;
    font-size: 12px; line-height:18px; text-align:center;
    transition: all 0.15s;
  }
  .close-btn:hover { background: rgba(255,80,80,0.25); color:#fff; }

  .grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 4px;
    padding: 2px 10px 4px;
  }

  .card {
    background: var(--card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 6px 8px;
  }
  .card.full { grid-column: 1 / -1; }

  .label {
    font-size: 8px; color: var(--dim);
    text-transform: uppercase; letter-spacing: 0.6px;
    margin-bottom: 1px;
  }

  .value {
    font-size: 14px; font-weight: 700;
    font-variant-numeric: tabular-nums;
  }

  .sub   { font-size: 8px; color: var(--dim); margin-top: 0; }

  .green  { color: var(--green); }
  .blue   { color: var(--blue); }
  .amber  { color: var(--amber); }
  .purple { color: var(--purple); }

  .progress-bar {
    margin-top: 4px; height: 2px;
    background: rgba(255,255,255,0.06);
    border-radius: 2px; overflow: hidden;
  }
  .progress-fill {
    height: 100%; border-radius: 2px;
    background: linear-gradient(90deg, var(--green), #22c55e);
    transition: width 0.5s ease;
  }


  .loading { text-align:center; padding:30px; color:var(--dim); }

  .footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 10px 6px;
    font-size: 9px;
    color: var(--dim);
  }
</style>
</head>
<body>

<div class="header">
  <span class="title"><span class="dot"></span>DeepSeek 用量</span>
  <button class="close-btn" onclick="pywebview.api.close_window()">&times;</button>
</div>

<div id="root">
  <div class="loading">加载中...</div>
</div>

<div class="footer">
  <span id="updated"></span>
  <span id="balance"></span>
</div>

<script>
const fmt = (n) => n.toLocaleString();

const render = (data) => {
  if (!data) {
    document.getElementById('root').innerHTML =
      '<div class="loading">今日暂无 API 请求</div>';
    return;
  }

  const hitRate = data.hit_rate.toFixed(1) + '%';

  document.getElementById('root').innerHTML = `
    <div class="grid">
      <div class="card">
        <div class="label">缓存命中率</div>
        <div class="value green">${hitRate}</div>
        <div class="progress-bar"><div class="progress-fill" style="width:${Math.min(data.hit_rate,100)}%"></div></div>
      </div>
      <div class="card">
        <div class="label">API 请求次数</div>
        <div class="value blue">${data.calls}</div>
        <div class="sub">今日累计</div>
      </div>
      <div class="card">
        <div class="label">缓存命中 Token</div>
        <div class="value green">${fmt(data.cache_hit)}</div>
        <div class="sub">¥0.025 / M tokens</div>
      </div>
      <div class="card">
        <div class="label">缓存未命中 Token</div>
        <div class="value amber">${fmt(data.cache_miss)}</div>
        <div class="sub">¥3.0 / M tokens</div>
      </div>
      <div class="card">
        <div class="label">输出 Token</div>
        <div class="value purple">${fmt(data.output)}</div>
        <div class="sub">¥6.0 / M tokens</div>
      </div>
      <div class="card">
        <div class="label">预估费用</div>
        <div class="value" style="color:#f87171">&yen;${data.cost.toFixed(2)}</div>
        <div class="sub">总计 ${fmt(data.grand_total)} tokens</div>
      </div>
    </div>
  `;

};
let loading = false;
async function refresh() {
  if (loading) return;
  loading = true;
  try {
    const data = await pywebview.api.get_stats();
    render(data);
    const now = new Date().toLocaleTimeString('zh-CN');
    document.getElementById('updated').textContent = '更新 ' + now;
  } catch(e) {
    console.error(e);
  } finally {
    loading = false;
  }
}

async function loadBalance() {
  try {
    const b = await pywebview.api.get_balance();
    document.getElementById('balance').textContent = '余额 ' + b;
  } catch(e) {}
}

render(__INIT_DATA__);
refresh();
loadBalance();
setInterval(refresh, REFRESH_MS);
setInterval(loadBalance, 300000);
</script>
</body>
</html>
""".replace("REFRESH_MS", str(REFRESH_SEC * 1000))


# ====================== API ======================

class Api:
    def __init__(self):
        self._cache = None
        self._lock = threading.Lock()

    def _refresh_cache(self):
        stats = parse_usage()
        with self._lock:
            self._cache = compute_display(stats)

    def get_stats(self):
        with self._lock:
            data = self._cache
        return data

    def get_balance(self):
        return get_balance()

    def close_window(self):
        window.destroy()


# ====================== main ======================

if __name__ == "__main__":
    api = Api()
    api._refresh_cache()  # preload before showing window

    def bg_refresh():
        while True:
            time.sleep(REFRESH_SEC)
            api._refresh_cache()

    threading.Thread(target=bg_refresh, daemon=True).start()

    # Inject initial data so first render is instant (no JS bridge round-trip)
    import json as _json
    initial_data = _json.dumps(api._cache or {}, ensure_ascii=False)
    html_with_data = HTML.replace('__INIT_DATA__', initial_data)

    window = webview.create_window(
        title="DeepSeek Token Monitor",
        html=html_with_data,
        js_api=api,
        width=270,
        background_color="#12161c",
        height=260,
        frameless=True,
        on_top=True,
        shadow=True,
        transparent=False,
        easy_drag=True,
    )
    webview.start(debug=False)

#!/usr/bin/env python3
"""DeepSeek Token Monitor — pywebview floating window for Claude Code usage.
v2: added skill invocation monitoring panel."""

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone, date
from pathlib import Path

import webview

# ====================== config ======================
CLAUDE_DIR   = Path.home() / ".claude/projects/C--Users-admin"
STATE_FILE   = CLAUDE_DIR / "ds_monitor_state.json"
SKILL_LOG    = Path.home() / ".claude/ds_monitor_active_skill.json"
REFRESH_SEC  = 1
BALANCE_SEC  = 30
# ====================================================

def get_balance_float():
    import urllib.request
    try:
        settings = Path.home() / ".claude/settings.json"
        with open(settings) as f:
            cfg = json.load(f)
        key = cfg.get("env", {}).get("ANTHROPIC_AUTH_TOKEN", "")
        if not key:
            return None
        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={"Authorization": f"Bearer {key}"}
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        for info in data.get("balance_infos", []):
            return float(info["total_balance"])
        return None
    except Exception:
        return None

def load_first_balance():
    try:
        if not STATE_FILE.exists():
            return None
        with open(STATE_FILE) as f:
            state = json.load(f)
        if state.get("date") == date.today().isoformat():
            return state.get("first_balance")
    except Exception:
        pass
    return None

def save_first_balance(balance):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"date": date.today().isoformat(), "first_balance": balance}, f)

def parse_usage():
    today_local = date.today()
    if not CLAUDE_DIR.exists():
        return None
    seen_mids = set()
    stats = {
        "calls": 0, "cache_hit": 0, "cache_miss": 0,
        "cache_create": 0, "output": 0,
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
                    ts = d.get("timestamp", "")
                    if not ts:
                        continue
                    try:
                        ts_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                        if ts_dt.astimezone().date() != today_local:
                            continue
                    except Exception:
                        continue
                    if d.get("type") != "assistant":
                        continue
                    usage = d.get("message", {}).get("usage", {})
                    if not usage:
                        continue
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

def compute_display(stats, cost=None):
    if stats is None or stats["calls"] == 0:
        return None
    s = stats
    prompt_total = s["cache_hit"] + s["cache_miss"] + s["cache_create"]
    grand_total  = prompt_total + s["output"]
    hit_rate = (s["cache_hit"] / prompt_total * 100) if prompt_total > 0 else 0
    return {
        "calls": s["calls"], "cache_hit": s["cache_hit"],
        "cache_miss": s["cache_miss"], "cache_create": s["cache_create"],
        "output": s["output"], "prompt_total": prompt_total,
        "grand_total": grand_total, "hit_rate": hit_rate, "cost": cost,
    }

def read_active_skill():
    """Read current active skill, return dict or None."""
    try:
        if not SKILL_LOG.exists():
            return None
        with open(SKILL_LOG, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("active"):
            return data
        return None
    except Exception:
        return None

# ====================== HTML UI ======================

HTML = r"""
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
    display: flex; flex-direction: column;
  }

  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 10px 2px;
    flex-shrink: 0;
  }

  .title {
    font-size: 11px; font-weight: 600; color: #e2e6ed; letter-spacing: 0.3px;
    display: flex; align-items: center; gap: 6px;
  }

  .title .dot {
    display: inline-block; width:5px; height:5px;
    border-radius:50%; background: var(--green);
    box-shadow: 0 0 6px var(--green);
  }

  /* ---- toggle switch ---- */
  .toggle-row { display: flex; align-items: center; gap: 5px; }
  .toggle-label { font-size: 8px; color: var(--dim); letter-spacing:0.3px; white-space:nowrap; }
  .toggle {
    -webkit-app-region: no-drag;
    position: relative; width: 28px; height: 14px; cursor: pointer;
  }
  .toggle input { opacity:0; width:0; height:0; }
  .toggle .slider {
    position: absolute; inset:0;
    background: rgba(255,255,255,0.1);
    border-radius: 14px; transition: 0.2s;
  }
  .toggle .slider::before {
    content:''; position:absolute; left:2px; top:2px;
    width:10px; height:10px; border-radius:50%;
    background: var(--dim); transition: 0.2s;
  }
  .toggle input:checked + .slider { background: rgba(96,165,250,0.3); }
  .toggle input:checked + .slider::before { transform:translateX(14px); background: var(--blue); }

  .header-right { display: flex; align-items: center; gap: 8px; }

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
    flex-shrink: 0;
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

  .sub { font-size: 8px; color: var(--dim); margin-top: 0; }

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

  /* ---- skills panel ---- */
  .skills-panel {
    margin: 4px 10px 4px;
    border-top: 1px solid var(--border);
    padding-top: 6px;
    flex-shrink: 0;
  }
  .skills-panel.hidden { display: none; }

  .skill-active-card {
    display: flex; align-items: center; gap: 8px;
    padding: 6px 8px;
    background: rgba(96,165,250,0.06);
    border: 1px solid rgba(96,165,250,0.15);
    border-radius: 6px;
  }
  .skill-active-dot {
    width: 6px; height: 6px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse 1.5s ease infinite;
    flex-shrink: 0;
  }
  @keyframes pulse {
    0%,100% { opacity:1; }
    50%     { opacity:0.4; }
  }
  .skill-active-info { flex:1; min-width:0; }
  .skill-active-name {
    font-size: 12px; font-weight: 700; color: var(--text);
    overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  }
  .skill-active-time {
    font-size: 9px; color: var(--dim); margin-top: 1px;
  }
  .skills-idle {
    text-align: center; padding: 6px;
    font-size: 10px; color: var(--dim);
  }

  .footer {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 2px 10px 6px;
    font-size: 9px; color: var(--dim);
    flex-shrink: 0;
  }
</style>
</head>
<body>

<div class="header">
  <span class="title"><span class="dot"></span>DeepSeek 用量</span>
  <div class="header-right">
    <div class="toggle-row">
      <span class="toggle-label">Skills</span>
      <label class="toggle">
        <input type="checkbox" id="skillToggle" onchange="onToggle(this.checked)">
        <span class="slider"></span>
      </label>
    </div>
    <button class="close-btn" onclick="pywebview.api.close_window()">&times;</button>
  </div>
</div>

<div id="root">
  <div class="loading">加载中...</div>
</div>

<div class="skills-panel hidden" id="skillsPanel">
  <div id="skillsContent">
    <div class="skills-idle">等待 Skill 调用...</div>
  </div>
</div>

<div class="footer">
  <span id="updated"></span>
  <span id="balance">__INIT_BALANCE__</span>
</div>

<script>
const fmt = (n) => n.toLocaleString();

let skillMonitorOn = false;

function onToggle(checked) {
  skillMonitorOn = checked;
  const panel = document.getElementById('skillsPanel');
  if (checked) {
    panel.classList.remove('hidden');
    refreshSkills();
  } else {
    panel.classList.add('hidden');
  }
  // save state
  try { pywebview.api.set_skill_monitor(checked); } catch(e) {}
}

const render = (data) => {
  if (!data) {
    document.getElementById('root').innerHTML =
      '<div class="loading">今日暂无 API 请求</div>';
    return;
  }
  const hitRate = data.hit_rate.toFixed(1) + '%';
  document.getElementById('root').innerHTML =
    '<div class="grid">' +
      '<div class="card">' +
        '<div class="label">缓存命中率</div>' +
        '<div class="value green">' + hitRate + '</div>' +
        '<div class="progress-bar"><div class="progress-fill" style="width:' + Math.min(data.hit_rate,100) + '%"></div></div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="label">API 请求次数</div>' +
        '<div class="value blue">' + data.calls + '</div>' +
        '<div class="sub">今日累计</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="label">缓存命中 Token</div>' +
        '<div class="value green">' + fmt(data.cache_hit) + '</div>' +
        '<div class="sub">&yen;0.025 / M tokens</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="label">缓存未命中 Token</div>' +
        '<div class="value amber">' + fmt(data.cache_miss) + '</div>' +
        '<div class="sub">&yen;3.0 / M tokens</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="label">输出 Token</div>' +
        '<div class="value purple">' + fmt(data.output) + '</div>' +
        '<div class="sub">&yen;6.0 / M tokens</div>' +
      '</div>' +
      '<div class="card">' +
        '<div class="label">今日费用</div>' +
        '<div class="value" style="color:#f87171">' + (data.cost != null ? '&yen;' + data.cost.toFixed(2) : '—') + '</div>' +
        '<div class="sub">总计 ' + fmt(data.grand_total) + ' tokens</div>' +
      '</div>' +
    '</div>';
};

function renderSkill(data) {
  const el = document.getElementById('skillsContent');
  if (!data || !data.active) {
    el.innerHTML = '<div class="skills-idle">等待 Skill 调用...</div>';
    return;
  }
  el.innerHTML =
    '<div class="skill-active-card">' +
      '<div class="skill-active-dot"></div>' +
      '<div class="skill-active-info">' +
        '<div class="skill-active-name">' + data.active + '</div>' +
        '<div class="skill-active-time">' + data.started + '</div>' +
      '</div>' +
    '</div>';
}

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

async function refreshSkills() {
  if (!skillMonitorOn) return;
  try {
    const data = await pywebview.api.get_active_skill();
    renderSkill(data);
  } catch(e) {}
}

async function loadBalance() {
  try {
    const b = await pywebview.api.get_balance();
    document.getElementById('balance').textContent = '余额 ' + b;
  } catch(e) {}
}

async function initToggle() {
  try {
    const state = await pywebview.api.get_skill_monitor_state();
    if (state) {
      document.getElementById('skillToggle').checked = true;
      onToggle(true);
    }
  } catch(e) {}
}

render(__INIT_DATA__);
refresh();
loadBalance();
initToggle();
setInterval(refresh, REFRESH_MS);
setInterval(refreshSkills, REFRESH_MS);
setInterval(loadBalance, BALANCE_MS);
</script>
</body>
</html>
""".replace("REFRESH_MS", str(REFRESH_SEC * 1000)).replace("BALANCE_MS", str(BALANCE_SEC * 1000))


# ====================== API ======================

class Api:
    def __init__(self):
        self._cache = None
        self._active_skill = None
        self._lock = threading.Lock()
        self._first_balance = None
        self._current_balance = None
        self._last_balance_fetch = 0
        self._skill_monitor_on = False

    def _init_balance(self):
        bal = get_balance_float()
        if bal is None:
            return
        saved = load_first_balance()
        if saved is not None:
            self._first_balance = saved
        else:
            save_first_balance(bal)
            self._first_balance = bal
        self._current_balance = bal
        self._last_balance_fetch = time.time()

    def _refresh_balance(self):
        now = time.time()
        if now - self._last_balance_fetch < BALANCE_SEC:
            return
        self._last_balance_fetch = now
        bal = get_balance_float()
        if bal is not None:
            self._current_balance = bal

    def _refresh_cache(self):
        self._refresh_balance()
        stats = parse_usage()
        cost = None
        if self._first_balance is not None and self._current_balance is not None:
            cost = self._first_balance - self._current_balance
        with self._lock:
            self._cache = compute_display(stats, cost)
            self._active_skill = read_active_skill()

    def get_stats(self):
        with self._lock:
            return self._cache

    def get_active_skill(self):
        with self._lock:
            return self._active_skill

    def get_balance(self):
        if self._current_balance is not None:
            return f"{self._current_balance:.2f} CNY"
        return "N/A"

    def set_skill_monitor(self, state):
        self._skill_monitor_on = bool(state)

    def get_skill_monitor_state(self):
        return self._skill_monitor_on

    def close_window(self):
        window.destroy()


# ====================== main ======================

if __name__ == "__main__":
    api = Api()
    api._init_balance()
    api._refresh_cache()

    def bg_refresh():
        while True:
            time.sleep(REFRESH_SEC)
            api._refresh_cache()

    threading.Thread(target=bg_refresh, daemon=True).start()

    import json as _json
    initial_data = _json.dumps(api._cache or {}, ensure_ascii=False)
    initial_balance = api.get_balance()
    html_with_data = HTML.replace('__INIT_DATA__', initial_data).replace('__INIT_BALANCE__', '余额 ' + initial_balance)

    window = webview.create_window(
        title="DeepSeek Token Monitor",
        html=html_with_data,
        js_api=api,
        width=270,
        background_color="#12161c",
        height=310,
        frameless=True,
        on_top=True,
        shadow=True,
        transparent=False,
        easy_drag=True,
    )
    webview.start(debug=False)

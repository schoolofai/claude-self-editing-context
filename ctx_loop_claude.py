#!/usr/bin/env python3
"""ctx_loop_claude.py: let Claude edit its own context, with the Claude Agent SDK.

Three ways to stay inside a small context budget on the same task (see logtask.py):
  --mode window    drop the oldest messages when over budget
  --mode summary   at 75% of the budget, ask for one summary that replaces the history
  --mode clm       the model edits its own context file with an edit_context tool

How the self-editing mode works:
  * Our code owns the context. Every harness turn is ONE fresh SDK query(); the prompt is the task line
    plus our rendered context ([[CTX_TURN i role=...]] blocks). The system prompt is replaced with ours.
  * The model gets exactly one tool, edit_context(code). It runs the model's Python against the context
    file (s = file text). A size check keeps the edit only if the context got smaller; the tool returns
    a one-line receipt: accepted (old -> new size) or rejected.
  * Budget warnings at 25 / 50 / 75 % plus an urgent one at 90 %, injected by a UserPromptSubmit hook.
  * If a new batch would push the context over budget, it is held back, the model gets one turn to
    shrink its context, then the batch is delivered (up to --retries times).
  * Tidy-up tool calls happen inside one query, so they don't count as task steps.
  * Final turn: a different system prompt, no tool server, no tools at all, one model call.
"""
import argparse, asyncio, json, os, re, shutil, subprocess, sys, tempfile, textwrap, time
import tiktoken
from claude_agent_sdk import (query, ClaudeAgentOptions, tool, create_sdk_mcp_server, HookMatcher,
                              AssistantMessage, ResultMessage, SystemMessage, TextBlock, ToolUseBlock, RateLimitEvent)
from logtask import make_task, QUESTION
from score import score

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--mode", choices=["window", "summary", "clm"], default="clm")
ap.add_argument("--model", default="sonnet", help="model alias or full name (default: sonnet)")
ap.add_argument("--effort", default="low", help="thinking effort: low / medium / high (default: low)")
ap.add_argument("--budget", type=int, default=2000, help="context budget in tokens (default: 2000)")
ap.add_argument("--batches", type=int, default=12)
ap.add_argument("--seed", type=int, default=11, help="11 -> 14 target IDs, 12 -> 6 target IDs")
ap.add_argument("--max-inner", type=int, default=6, help="max model calls inside one harness turn")
ap.add_argument("--retries", type=int, default=3, help="hold-back-and-shrink attempts before dropping old turns")
ap.add_argument("--show", action="store_true", help="live view: budget bar, the model's edit code, receipts")
ap.add_argument("--log", default=None, help="write a JSON log of the run here")
ap.add_argument("--workdir", default=None, help="folder for LIVE_CTX.txt (default: a new temp folder)")
ap.add_argument("--no-edit-batches", type=int, default=0,
                help="testing: withhold the edit tool for the first N batches, to force the over-budget path")
args = ap.parse_args()

ENC = tiktoken.get_encoding("o200k_base")
def tok(s): return len(ENC.encode(s))

# ---------------------------------------------------------------- terminal output
TTY = sys.stdout.isatty() or args.show
C = dict(dim="\033[2m", b="\033[1m", g="\033[32m", r="\033[31m", y="\033[33m", c="\033[36m", m="\033[35m",
         bl="\033[34m", x="\033[0m") if TTY else {k: "" for k in "dim b g r y c m bl x".split()}
WIDTH = min(100, shutil.get_terminal_size((100, 30)).columns)

class Out:
    """Line printer with an optional 'waiting for Claude' ticker (only in --show mode)."""
    def __init__(self): self.tick_task, self.tick_label, self.t0 = None, "", 0.0
    def _clear(self):
        if self.tick_task: sys.stdout.write("\r\033[K")
    def say(self, tag, msg, col="x"):
        self._clear(); print(f"{C[col]}{tag:<8}{C['x']} {msg}", flush=True); self._redraw()
    def raw(self, line):
        self._clear(); print(line, flush=True); self._redraw()
    def _redraw(self):
        if self.tick_task:
            sys.stdout.write(f"{C['dim']}{'':<8} {self.tick_label} {time.time() - self.t0:4.1f}s{C['x']}"); sys.stdout.flush()
    async def _tick(self):
        frames = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"; k = 0
        while True:
            sys.stdout.write(f"\r\033[K{C['dim']}{'':<8} {frames[k % 10]} {self.tick_label} {time.time() - self.t0:4.1f}s{C['x']}")
            sys.stdout.flush(); k += 1; await asyncio.sleep(0.1)
    def start(self, label):
        if not args.show: return
        self.tick_label, self.t0 = label, time.time()
        self.tick_task = asyncio.get_running_loop().create_task(self._tick())
    def stop(self):
        if self.tick_task:
            self.tick_task.cancel(); self.tick_task = None; sys.stdout.write("\r\033[K"); sys.stdout.flush()
out = Out()

def bar(n, budget, width=34):
    fill = min(width, round(width * n / budget)); pct = n / budget
    colr = C["g"] if pct < 0.5 else (C["y"] if pct < 0.9 else C["r"])
    return f"{colr}{'█' * fill}{C['dim']}{'░' * (width - fill)}{C['x']} ~{n:,} / {budget:,}"

def show_code(code):
    if not args.show: return
    for ln in code.strip("\n").split("\n")[:8]:
        for i, part in enumerate(textwrap.wrap(ln, WIDTH - 12, break_long_words=True, replace_whitespace=False) or [""]):
            out.raw(f"{'':<8}   {C['bl']}{'' if i == 0 else '  '}{part}{C['x']}")

# ---------------------------------------------------------------- task + prompts
task = make_task(args.seed, args.batches)
WORK = args.workdir or tempfile.mkdtemp(prefix=f"ctx-{args.mode}-s{args.seed}-")
os.makedirs(WORK, exist_ok=True)
CTX = os.path.join(WORK, "LIVE_CTX.txt")

BASE = ("You are a log-triage assistant inside a harness. Log batches arrive one at a time. After the last "
        "batch you will be asked for the request IDs (req=...) of every billing [ERROR] line across ALL batches. "
        "After handling a batch, reply with exactly READY.")
CLM_RULES = f"""

## Managing your context
Your context budget is {args.budget} tokens. The header shows your current size; every edit result shows the new size.
Everything after the task line IS the file {CTX}: one [[CTX_TURN i role=...]] block per message.
It is already in front of you, so never print the file.
To free up space, call the edit_context tool. It runs your Python with `re`, `p` (the path) and `s` (the file text)
already defined; change `s` and it is saved. Find text with code; never type log lines out yourself. Example, after batch 3:
    m = re.search(r"\\[\\[CTX_TURN \\d+ role=user\\]\\]\\n=== log batch 3/{args.batches} ===.*?(?=\\n\\[\\[CTX_TURN|\\Z)", s, flags=re.S)
    ids = re.findall(r"\\[ERROR\\] billing req=([0-9a-f]{{8}})", m.group(0))
    s = s.replace(m.group(0), m.group(0).split("\\n")[0] + "\\nbatch 3 done: billing ERROR ids: " + (", ".join(ids) or "none"))
Keep one running note of every billing ERROR id found so far, and COPY facts forward when you replace text:
pull ids out with code and never lose one. Keep the [[CTX_TURN ...]] header of every turn you keep; an empty turn is dropped.
An edit is only accepted if the context gets smaller. Prefer one big tidy-up to many small edits.
Edits apply from your next turn. Then reply READY."""
NO_TOOLS = " Do not run any commands or read any files. Answer from the context below only."
PINNED_SYS = BASE + (CLM_RULES if args.mode == "clm" else NO_TOOLS)
PINNED_TASK = f"Task: triage {args.batches} log batches, then answer one question."
ANSWER_SYS = (BASE + " This is the final turn: every batch has arrived. You have no tools and cannot edit anything. "
              "Answer the question from the context below only.")
SUMMARY_ASK = ("\n\n[[HARNESS]]\nSummarize the conversation so far in one message. "
               "Keep every fact needed for the final question.")

# ---------------------------------------------------------------- the editable context
turns = []   # list of {"role", "content"}; this is what the model sees after the task line
HDR = re.compile(r"^\[\[CTX_TURN\s+(\d+)\s+role=([a-z]+)\]\]\s*$", re.M)
def render(ts): return "\n\n".join(f"[[CTX_TURN {i} role={m['role']}]]\n{m['content']}" for i, m in enumerate(ts, 1))
def parse(text):
    """Surviving headers define the turns. Text before the first header (or a file with no headers left)
    is kept as a user note, so deleting every header can't silently wipe the context."""
    res, ms = [], list(HDR.finditer(text))
    lead = text[: ms[0].start() if ms else len(text)].strip()
    if lead: res.append({"role": "user", "content": lead})
    for k, mt in enumerate(ms):
        body = text[mt.end(): ms[k + 1].start() if k + 1 < len(ms) else len(text)].strip()
        if body: res.append({"role": "assistant" if mt.group(2) == "assistant" else "user", "content": body})
    return res
def size(ts=None, sys_txt=None): return tok(sys_txt or PINNED_SYS) + tok(PINNED_TASK) + tok(render(turns if ts is None else ts))

stats = dict(steps=0, queries=0, model_calls=0, input=0, cache_write=0, cache_read=0, output=0, cost_usd=0.0,
             tool_calls=0, edits=0, rejected=0, unchanged=0, tool_errors=0, denied_tools=0, nudges=0,
             rollbacks=0, hard_dropped=0, precompact_fired=0, dropped=0, summaries=0)
calls_log = []
STATE = dict(nudge=None, nudged=set(), receipts=[], kind=None, batch_no=0)

# ---------------------------------------------------------------- the edit tool, with the size check
PRELUDE = "import re, json\np = {p!r}\ns = open(p).read()\n_orig = s\n"
EPILOGUE = "\nif open(p).read() == _orig and s != _orig:\n    open(p, 'w').write(s)\n"

@tool("edit_context", "Edit your live context file LIVE_CTX.txt with Python. `re`, `p` (path) and `s` (file text) are "
      "predefined; modify `s` and it is saved. The edit is kept only if it makes the context smaller.", {"code": str})
async def edit_context(a):
    code = a.get("code", "")
    old_text, before = open(CTX).read(), size()
    r = await asyncio.to_thread(subprocess.run, [sys.executable, "-c", PRELUDE.format(p=CTX) + code + EPILOGUE],
                                capture_output=True, text=True, timeout=30, cwd=WORK)
    printed = (r.stdout or "").strip()
    err = (r.stderr.strip().splitlines() or [""])[-1] if r.returncode else ""
    stats["tool_calls"] += 1; stats["tool_errors"] += bool(err)
    new_text = open(CTX).read()
    if new_text == old_text:
        stats["unchanged"] += 1
        receipt = f"[LIVE_CTX.txt unchanged: your code changed nothing (~{before}/{args.budget} tokens)]"
    else:
        new = parse(new_text); after = size(new)
        if not new:
            stats["rejected"] += 1; open(CTX, "w").write(old_text)
            receipt = (f"[LIVE_CTX.txt edit rejected: it would delete your whole context, so it was undone "
                       f"(~{before} tokens). Replace old text with a shorter note instead.]")
        elif after < before:
            turns[:] = new; stats["edits"] += 1
            open(CTX, "w").write(render(turns))            # normalise headers and numbering
            receipt = f"[LIVE_CTX.txt edit accepted: ~{before} -> ~{after}/{args.budget} tokens]"
        else:
            stats["rejected"] += 1; open(CTX, "w").write(old_text)
            receipt = (f"[LIVE_CTX.txt edit rejected: it did not make the context smaller (~{before} -> ~{after} tokens), "
                       f"so it was undone. Replace old text with a shorter note instead of adding to it.]")
    STATE["receipts"].append(receipt)
    out.say("harness", ("✓ " if "accepted" in receipt else ("✗ " if "rejected" in receipt else "· ")) + receipt[:WIDTH - 12],
            "g" if "accepted" in receipt else ("r" if "rejected" in receipt else "y"))
    body = ((printed[:300] + ("…" if len(printed) > 300 else "")) + "\n" if printed else "") + \
           (f"[python error: {err}]\n" if err else "") + receipt
    return {"content": [{"type": "text", "text": body}]}

SERVER = create_sdk_mcp_server("ctx", tools=[edit_context])
EDIT_TOOL = "mcp__ctx__edit_context"

# ---------------------------------------------------------------- budget warnings, delivered by a hook
NOTE_RULE = ("When you replace text with a note, copy the facts forward: every billing ERROR req id exactly as "
             "written, plus a NEXT line.")
def warning_for(n):
    B = args.budget
    STATE["nudged"] = {f for f in STATE["nudged"] if n >= int(B * f)}      # re-arm after the context shrinks
    if n >= int(0.9 * B):
        return (f"BUDGET WARNING (URGENT): {n}/{B} tokens, over 90% of your limit. Shrink your context now and do "
                "nothing else. Going over the limit rolls back your newest turn.")
    crossed = [f for f in (0.25, 0.5, 0.75) if n >= int(B * f)]
    fire = [f for f in crossed if f not in STATE["nudged"]]
    if not fire: return None
    tier = max(fire); STATE["nudged"] |= set(crossed); pct = int(tier * 100)
    if tier <= 0.25:
        return f"BUDGET WARNING: about {pct}% of your {B}-token budget used ({n} tokens). Nothing to do yet."
    if tier <= 0.5:
        return (f"BUDGET WARNING: about {pct}% of your {B}-token budget used ({n} tokens). Finish what you are doing, "
                "then tidy up once. " + NOTE_RULE)
    return (f"BUDGET WARNING: about {pct}% of your {B}-token budget used ({n} tokens), nearly full. Compress finished "
            "parts now, but don't throw facts away. " + NOTE_RULE + " Going over the limit rolls back your newest turn.")

async def hook_prompt(inp, tool_use_id, ctx):
    w = STATE["nudge"]
    return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": w}} if w else {}
async def hook_pretool(inp, tool_use_id, ctx):
    if inp.get("tool_name") != EDIT_TOOL:
        stats["denied_tools"] += 1
        return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                       "permissionDecisionReason": "Only edit_context is allowed."}}
    return {}
async def hook_precompact(inp, tool_use_id, ctx):
    stats["precompact_fired"] += 1
    return {"decision": "block", "reason": "This harness manages the context itself."}
HOOKS = {"UserPromptSubmit": [HookMatcher(hooks=[hook_prompt])],
         "PreToolUse": [HookMatcher(hooks=[hook_pretool])],
         "PreCompact": [HookMatcher(hooks=[hook_precompact])]}

class UsageLimit(Exception): pass

# ---------------------------------------------------------------- one fresh SDK query per harness turn
async def call_model(kind):
    final = kind == "question"
    no_edit = (args.mode != "clm" or kind == "summary"
               or (kind == "batch" and STATE["batch_no"] <= args.no_edit_batches))
    sys_txt = ANSWER_SYS if final else PINNED_SYS
    if no_edit and args.mode == "clm" and not final:
        sys_txt += "\n(Testing: the edit tool is switched off for this turn. Just reply READY.)"
    body = render(turns) + (SUMMARY_ASK if kind == "summary" else "")
    prompt = f"{PINNED_TASK}\n\n=== YOUR CONTEXT (~{size(sys_txt=sys_txt)}/{args.budget} tokens) ===\n{body}\n"
    opts = ClaudeAgentOptions(
        system_prompt=sys_txt,                       # replaces Claude Code's default system prompt
        tools=[],                                    # no built-in tools: no Bash, no Read, no Write
        mcp_servers={} if (final or no_edit) else {"ctx": SERVER},
        strict_mcp_config=True,
        allowed_tools=[] if (final or no_edit) else [EDIT_TOOL],
        permission_mode="dontAsk",                   # anything not pre-approved is denied
        setting_sources=[],                          # ignore user/project settings and CLAUDE.md
        verbatim_prompts=True,                       # send our prompt exactly as written
        hooks=HOOKS,
        model=args.model, effort=args.effort or None,
        max_turns=1 if (final or no_edit) else args.max_inner,
        cwd=WORK,
        env={"CLAUDE_CODE_SKIP_PROMPT_HISTORY": "1"},
        extra_args={"no-session-persistence": None},
    )
    STATE["kind"], STATE["receipts"] = kind, []
    text, tools_offered, uses, res, t0 = "", None, [], None, time.time()
    out.start({"summary": "summarising", "question": "answering"}.get(kind, "Claude is working"))
    try:
        async for msg in query(prompt=prompt, options=opts):
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                tools_offered = msg.data.get("tools")
            elif isinstance(msg, AssistantMessage):
                for bl in msg.content:
                    if isinstance(bl, TextBlock): text = bl.text
                    elif isinstance(bl, ToolUseBlock):
                        code = bl.input.get("code", json.dumps(bl.input)); uses.append(code[:1500])
                        out.say("claude", "calls edit_context:", "m"); show_code(code)
            elif isinstance(msg, RateLimitEvent) and msg.rate_limit_info.status == "rejected":
                raise UsageLimit("usage limit reached")
            elif isinstance(msg, ResultMessage):
                res = msg
    finally:
        out.stop()
    if res and res.is_error and res.api_error_status == 429: raise UsageLimit("HTTP 429")
    u = (res.usage or {}) if res else {}
    stats["queries"] += 1; stats["model_calls"] += (res.num_turns if res else 0)
    for k, f in (("input", "input_tokens"), ("cache_write", "cache_creation_input_tokens"),
                 ("cache_read", "cache_read_input_tokens"), ("output", "output_tokens")):
        stats[k] += u.get(f, 0) or 0
    stats["cost_usd"] += (res.total_cost_usd or 0.0) if res else 0.0
    calls_log.append(dict(kind=kind, ctx=size(), tools_offered=tools_offered, warning=STATE["nudge"], code=uses,
                          receipts=list(STATE["receipts"]), reply=text[:500],
                          usage={k: u.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")},
                          num_turns=res.num_turns if res else None, secs=round(time.time() - t0, 1)))
    return text.strip(), u, uses

def model_line(before, u, text, uses=None):
    fresh = (u.get("input_tokens", 0) or 0) + (u.get("cache_creation_input_tokens", 0) or 0)
    if args.show:
        out.say("claude", f"replies {text[:70]!r}", "c")
    else:
        out.say("model", f"{C['dim']}ctx ~{before:>4}/{args.budget} | fresh {fresh:>5} tok (cache read "
                         f"{u.get('cache_read_input_tokens', 0):>6})" + (f" | {len(uses)} edit call(s)" if uses is not None else "")
                         + f"{C['x']} | reply {text[:60]!r}", "m" if uses else "c")

async def run_turn(kind):
    final = kind == "question"
    if kind in ("batch", "question"): stats["steps"] += 1
    if args.mode == "window":                                          # drop the oldest turns while over budget
        d0, s0 = stats["dropped"], size()
        while turns and size() > args.budget: turns.pop(0); stats["dropped"] += 1
        while turns and turns[0]["role"] == "assistant": turns.pop(0); stats["dropped"] += 1
        if stats["dropped"] > d0:
            out.say("harness", f"window full: dropped the {stats['dropped'] - d0} oldest turn(s)  ~{s0:,} -> ~{size():,} tokens", "r")
    if args.mode == "summary" and size() > 0.75 * args.budget:         # one summary replaces the history
        before = size()
        out.say("harness", f"over 75% of budget: asking for a summary", "y")
        text, u, _ = await call_model("summary")
        turns[:] = [{"role": "user", "content": "[SUMMARY OF EARLIER TURNS]\n" + text}]
        stats["summaries"] += 1
        out.say("harness", f"summary replaces the history: ~{before:,} -> ~{size():,} tokens", "y")
    if args.mode != "clm":
        STATE["nudge"] = None
        before = size(sys_txt=ANSWER_SYS if final else None)
        if args.show and not final: out.raw(f"{'context':<8} {bar(before, args.budget)}")
        text, u, _ = await call_model(kind)
        model_line(before, u, text)
        turns.append({"role": "assistant", "content": text or "READY"})
        return text
    STATE["nudge"] = None if final else warning_for(size())
    if STATE["nudge"]:
        stats["nudges"] += 1
        out.say("hook", STATE["nudge"][:WIDTH - 12], "y")
    open(CTX, "w").write(render(turns))
    before = size(sys_txt=ANSWER_SYS if final else None)
    if args.show and not final: out.raw(f"{'context':<8} {bar(before, args.budget)}")
    text, u, uses = await call_model(kind)
    model_line(before, u, text, uses)
    if args.show and uses and not final: out.raw(f"{'context':<8} {bar(size(), args.budget)}")
    turns.append({"role": "assistant", "content": text or "READY"})
    if not final and calls_log[-1]["receipts"]:
        turns.append({"role": "user", "content": calls_log[-1]["receipts"][-1]})
    return text

async def main():
    label = {"window": "drop oldest", "summary": "summarise at 75%", "clm": "self-editing"}[args.mode]
    out.say("setup", f"Claude Agent SDK · model {args.model} ({args.effort}) · mode: {label} · budget {args.budget:,} tokens · "
                     f"{args.batches} batch{'es' if args.batches != 1 else ''}", "b")
    t0 = time.time(); ans, err = "", None
    try:
        for i in range(1, args.batches + 1):
            batch = {"role": "user", "content": task.batch_text(i)}
            out.say("env", f"log batch {i}/{args.batches} arrives (+{tok(chr(10).join(task.batches[i - 1]))} tokens)", "dim")
            tries = 0
            while args.mode == "clm" and size(turns + [batch]) > args.budget and tries < args.retries:
                tries += 1; stats["rollbacks"] += 1
                turns.append({"role": "user", "content":
                    f"[HARNESS NOTICE: CONTEXT LIMIT, attempt {tries}/{args.retries}] Batch {i} would bring your context "
                    f"to ~{size(turns + [batch])}/{args.budget} tokens, so it was held back; it will be sent again after "
                    "this turn. SHRINK YOUR CONTEXT THIS TURN and do nothing else: swap old regions for short notes and "
                    "copy every billing ERROR id forward."})
                out.say("harness", f"batch {i} would go over budget: held back, shrink-only turn ({tries}/{args.retries})", "r")
                await run_turn("shrink")
            while args.mode == "clm" and size(turns + [batch]) > args.budget and turns:     # last resort
                turns.pop(0); stats["hard_dropped"] += 1
            turns.append(batch); STATE["batch_no"] = i
            await run_turn("batch")
        turns.append({"role": "user", "content": QUESTION})
        out.say("env", "the question arrives: list every billing ERROR req ID (final turn, no tools)", "dim")
        ans = await run_turn("question")
    except UsageLimit as e:
        err = f"stopped: {e}"; out.say("error", err, "r")
    except Exception as e:
        err = f"error: {type(e).__name__}: {str(e)[:300]}"; out.say("error", err, "r")
    wall = time.time() - t0
    r = score(ans, task.truth, task.truth_by_batch)
    if args.show and ans:
        for i, part in enumerate(textwrap.wrap(", ".join(re.findall(r"\b[0-9a-f]{8}\b", ans)), WIDTH - 12)):
            out.say("answer" if i == 0 else "", part, "c")
    out.say("result", f"{C['g'] if r['ok'] else C['r']}found {r['found']}/{r['total']} IDs, {len(r['wrong'])} wrong  "
                      f"{'CORRECT' if r['ok'] else 'WRONG'}{C['x']}", "b")
    if r["missed_batches"]: out.say("result", f"missed IDs came from batches {r['missed_batches']}", "dim")
    read = stats["input"] + stats["cache_write"] + stats["cache_read"]
    out.say("cost", f"{stats['steps']} steps · {stats['model_calls']} model calls · {read:,} tokens read "
                    f"({stats['cache_read']:,} cached) · {wall:.0f}s · est. API price ${stats['cost_usd']:.2f}", "b")
    if args.mode == "clm":
        out.say("edits", f"{stats['edits']} accepted, {stats['rejected']} rejected, {stats['rollbacks']} held back, "
                         f"{stats['nudges']} warnings", "dim")
    if args.log:
        json.dump(dict(engine="claude-agent-sdk", mode=args.mode, model=args.model, effort=args.effort, budget=args.budget,
                       batches=args.batches, seed=args.seed, truth=task.truth, truth_by_batch=task.truth_by_batch,
                       answer=ans, **{k: r[k] for k in ("found", "total", "wrong", "missed_batches", "ok")}, error=err,
                       wall_secs=round(wall, 1), **stats, calls_log=calls_log, final_context=render(turns),
                       system_prompt=PINNED_SYS.replace(WORK, "WORKDIR")), open(args.log, "w"), indent=1)
    if not args.workdir: shutil.rmtree(WORK, ignore_errors=True)

if __name__ == "__main__":
    asyncio.run(main())

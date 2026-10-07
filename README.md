# Let Claude edit its own context

**Give Claude one tool to rewrite its own context, and it keeps every fact inside a budget a plain
"drop the oldest messages" harness can't handle.** A small, runnable
[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview) harness built on the ideas in
Meta's *Context Language Models* paper.

[![Watch the video: Meta Context Language Models - Start Using it](https://img.youtube.com/vi/d-Pj4E4Z0ME/maxresdefault.jpg)](https://youtu.be/d-Pj4E4Z0ME)

▶ **Video:** [Meta Context Language Models - Start Using it](https://youtu.be/d-Pj4E4Z0ME) (17 min). Every step
below links to the moment in the video where it's explained.

---

## Quick start

### 0. Setup

You need **Python 3.10 or newer** (tested with 3.13) and either a **Claude Code login** or an
**Anthropic API key**.

```bash
git clone https://github.com/schoolofai/claude-self-editing-context
cd claude-self-editing-context
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt      # claude-agent-sdk, tiktoken
```

**Log in, using one of these:**

- **Claude Code login:** install [Claude Code](https://code.claude.com/docs), run `claude` once and sign in.
  The Agent SDK uses that login when no API key is set. Usage counts toward your plan.
- **API key:** `export ANTHROPIC_API_KEY=...`. The SDK uses it when it's set, and you pay API prices.

Check the install without calling the model:

```bash
python test_offline.py        # should end with: ALL PASS
```

**The task.** Twelve batches of synthetic server logs arrive one at a time (`logtask.py`). At the end
Claude must list the request ID of every billing `[ERROR]` line: 14 IDs with the default seed. It knows
the question from the start. The logs add up to about 4,700 tokens against a **2,000-token budget**, so
something has to give. `python logtask.py` prints the logs and the answer key.

All runs below use **Claude Sonnet at low effort** (the defaults). Prices are the SDK's estimate at API
rates. The sample outputs are real, trimmed with `…`. Your model's edits and numbers will vary a little
from run to run.

---

### 1. A running note the model rewrites, not appends

[▶ Watch this step (7:09)](https://youtu.be/d-Pj4E4Z0ME?t=429) · [in a coding agent (7:40)](https://youtu.be/d-Pj4E4Z0ME?t=460)

**How it's implemented.** One rule in the system prompt (`ctx_loop_claude.py`, `CLM_RULES`, lines 101–115):

```python
Keep one running note of every billing ERROR id found so far, and COPY facts forward when you replace text:
pull ids out with code and never lose one. Keep the [[CTX_TURN ...]] header of every turn you keep; an empty turn is dropped.
```

**Run it.** This is a 3-batch self-editing run that keeps the context file in `ctx-demo/` so you can read it afterwards:

```bash
python ctx_loop_claude.py --batches 3 --show --workdir ctx-demo --log runs/clm-3.json
cat ctx-demo/LIVE_CTX.txt
```

**What to expect** (about 20 s, 7 model calls, about $0.04). After each batch Claude replaces the raw
log lines with a one-line note:

```text
setup    Claude Agent SDK · model sonnet (low) · mode: self-editing · budget 2,000 tokens · 3 batches
env      log batch 1/3 arrives (+395 tokens)
hook     BUDGET WARNING: about 25% of your 2000-token budget used (845 tokens). Nothing to do yet
context  ██████████████░░░░░░░░░░░░░░░░░░░░ ~845 / 2,000
claude   calls edit_context:
           m = re.search(r"\[\[CTX_TURN \d+ role=user\]\]\n=== log batch 1/3
             ===.*?(?=\n\[\[CTX_TURN|\Z)", s, flags=re.S)
           ids = re.findall(r"\[ERROR\] billing req=([0-9a-f]{8})", m.group(0))
           s = s.replace(m.group(0), m.group(0).split("\n")[0] + "\n=== log batch 1/3 ===\nbatch 1
             done: billing ERROR ids: " + (", ".join(ids) or "none"))
harness  ✓ [LIVE_CTX.txt edit accepted: ~845 -> ~472/2000 tokens]
…
answer   5387f613, 1b98fbe4, cc099a1e, f1b9ab7c, 6111b4b5
result   found 5/5 IDs, 0 wrong  CORRECT
cost     4 steps · 7 model calls · 16,641 tokens read (9,427 cached) · 18s · est. API price $0.04
edits    3 accepted, 0 rejected, 0 held back, 1 warnings
```

`ctx-demo/LIVE_CTX.txt` now holds notes instead of 42 log lines:

```text
[[CTX_TURN 1 role=user]]
=== log batch 1/3 ===
batch 1 done: billing ERROR ids: 5387f613, 1b98fbe4

[[CTX_TURN 2 role=assistant]]
READY

[[CTX_TURN 3 role=user]]
[LIVE_CTX.txt edit accepted: ~845 -> ~472/2000 tokens]

[[CTX_TURN 4 role=user]]
batch 2 done: billing ERROR ids: cc099a1e
…
[[CTX_TURN 7 role=user]]
batch 3 done: billing ERROR ids: f1b9ab7c, 6111b4b5
…
[[CTX_TURN 10 role=user]]
QUESTION: List the request IDs (the req=... value) of EVERY [ERROR] line from service "billing", …
```

For coding agents, the same idea is a `NOTES.md` rule. See step 9 and `agents-md/NOTES_RULE.md`.

---

### 2. One edit tool, with a size check and a receipt

[▶ Watch this step (8:29)](https://youtu.be/d-Pj4E4Z0ME?t=509) · [the size check (8:44)](https://youtu.be/d-Pj4E4Z0ME?t=524)

**How it's implemented.** `edit_context` in `ctx_loop_claude.py` (lines 150–183) is an in-process Agent SDK
custom tool. It runs Claude's Python with `s` set to the context file text, reads the file back, and
keeps the edit only if the context got smaller. Either way it returns a one-line receipt:

```python
@tool("edit_context", "Edit your live context file LIVE_CTX.txt with Python. ...", {"code": str})
async def edit_context(a):
    ...
        new = parse(new_text); after = size(new)
        if not new:                      # an edit that wipes the file is refused
            receipt = "[LIVE_CTX.txt edit rejected: it would delete your whole context, so it was undone ...]"
        elif after < before:             # the size check
            turns[:] = new
            receipt = f"[LIVE_CTX.txt edit accepted: ~{before} -> ~{after}/{args.budget} tokens]"
        else:
            receipt = "[LIVE_CTX.txt edit rejected: it did not make the context smaller ...]"
```

`parse` (lines 128–137) rebuilds the turns from the surviving `[[CTX_TURN i role=...]]` headers. Text
left without a header is kept as a note, so a sloppy edit can't silently wipe the context.

**Run it.** The live receipts are the `harness ✓ [LIVE_CTX.txt edit accepted ...]` lines in step 1. To
see the rejection paths without calling the model, run step 8: `python test_offline.py`.

---

### 3. An answer-only final turn

[▶ Watch this step (9:18)](https://youtu.be/d-Pj4E4Z0ME?t=558)

**How it's implemented.** On the question turn, `call_model` (lines 228–283) switches to a separate system
prompt (`ANSWER_SYS`, line 119), removes the tool server and allows exactly one model call:

```python
ANSWER_SYS = (BASE + " This is the final turn: every batch has arrived. You have no tools and cannot edit anything. "
              "Answer the question from the context below only.")
...
        mcp_servers={} if (final or no_edit) else {"ctx": SERVER},
        allowed_tools=[] if (final or no_edit) else [EDIT_TOOL],
        max_turns=1 if (final or no_edit) else args.max_inner,
```

**Run it.** Read the log from step 1 to see which tools Claude was offered on each turn:

```bash
python -c "
import json
d = json.load(open('runs/clm-3.json'))
for c in d['calls_log']:
    print(f\"{c['kind']:<9} tools offered: {c['tools_offered']}  model calls: {c['num_turns']}  edits: {len(c['code'])}\")
print('steps:', d['steps'], ' model calls:', d['model_calls'], ' denied tool calls:', d['denied_tools'])
"
```

**What to expect:**

```text
batch     tools offered: ['mcp__ctx__edit_context']  model calls: 2  edits: 1
batch     tools offered: ['mcp__ctx__edit_context']  model calls: 2  edits: 1
batch     tools offered: ['mcp__ctx__edit_context']  model calls: 2  edits: 1
question  tools offered: []  model calls: 1  edits: 0
steps: 4  model calls: 7  denied tool calls: 0
```

The question turn has no tools at all and one model call, so Claude can't tidy its notes "one more
time" instead of answering.

---

### 4. Budget warnings at 25 / 50 / 75%, an urgent one at 90%, and rollback

[▶ Watch this step (11:24)](https://youtu.be/d-Pj4E4Z0ME?t=684) · [the warnings (11:43)](https://youtu.be/d-Pj4E4Z0ME?t=703) · [the rollback test (12:11)](https://youtu.be/d-Pj4E4Z0ME?t=731)

**How it's implemented.**

- Every prompt header and every receipt shows the current size (`~845/2000 tokens`).
- `warning_for` (lines 191–207) picks the warning text, and a `UserPromptSubmit` hook adds it to the turn:

  ```python
  async def hook_prompt(inp, tool_use_id, ctx):
      w = STATE["nudge"]
      return {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": w}} if w else {}
  ```

- If a new batch would push the context over budget, `main` (lines 343–354) holds it back and gives
  Claude a shrink-only turn. It retries up to `--retries` times, then drops the oldest turns as a last resort:

  ```python
  while args.mode == "clm" and size(turns + [batch]) > args.budget and tries < args.retries:
      turns.append({"role": "user", "content": f"[HARNESS NOTICE: CONTEXT LIMIT, attempt {tries}/{args.retries}] "
                    f"Batch {i} would bring your context to ~{size(turns + [batch])}/{args.budget} tokens, so it was held back; ..."})
      await run_turn("shrink")
  ```

**Run it (a). The warning texts, with no model calls:**

```bash
python ctx_loop_claude.py --show-warnings
```

```text
 10% (~200 tokens): (no warning)
 25% (~500 tokens): BUDGET WARNING: about 25% of your 2000-token budget used (500 tokens). Nothing to do yet.
 50% (~1000 tokens): BUDGET WARNING: about 50% of your 2000-token budget used (1000 tokens). Finish what you are doing, then tidy up once. When you replace text with a note, copy the facts forward: every billing ERROR req id exactly as written, plus a NEXT line.
 75% (~1500 tokens): BUDGET WARNING: about 75% of your 2000-token budget used (1500 tokens), nearly full. Compress finished parts now, but don't throw facts away. When you replace text with a note, copy the facts forward: every billing ERROR req id exactly as written, plus a NEXT line. Going over the limit rolls back your newest turn.
 90% (~1800 tokens): BUDGET WARNING (URGENT): 1800/2000 tokens, over 90% of your limit. Shrink your context now and do nothing else. Going over the limit rolls back your newest turn.
  after an edit drops the context back under 50%, the 50% and 75% warnings re-arm: (no warning) -> BUDGET WARNING: about 50% of your 2000-token budget used (1200 tokens). Finish what you are doing, then tidy up once. ...
```

**Run it (b). Force a rollback.** Sonnet tidies every turn, so a normal run never goes over budget.
`--no-edit-batches 3` withholds the edit tool for the first three batches, so batch 4 has to be held back:

```bash
python ctx_loop_claude.py --batches 5 --no-edit-batches 3 --show --log runs/forced.json
```

**What to expect** (about 40 s, 10 model calls, about $0.09):

```text
env      log batch 1/5 arrives (+395 tokens)
hook     BUDGET WARNING: about 25% of your 2000-token budget used (843 tokens). Nothing to do yet
…
env      log batch 3/5 arrives (+392 tokens)
hook     BUDGET WARNING: about 75% of your 2000-token budget used (1687 tokens), nearly full. Com
context  █████████████████████████████░░░░░ ~1,687 / 2,000
claude   replies 'READY'
env      log batch 4/5 arrives (+394 tokens)
harness  batch 4 would go over budget: held back, shrink-only turn (1/3)
context  ██████████████████████████████░░░░ ~1,781 / 2,000
claude   calls edit_context:
           …
harness  ✓ [LIVE_CTX.txt edit accepted: ~1781 -> ~643/2000 tokens]
claude   replies 'READY'
context  ███████████░░░░░░░░░░░░░░░░░░░░░░░ ~643 / 2,000
context  ███████████████████░░░░░░░░░░░░░░░ ~1,094 / 2,000
…
answer   5387f613, 1b98fbe4, cc099a1e, f1b9ab7c, 6111b4b5, 78aa8105, 88ebd524, 8ccda80c
result   found 8/8 IDs, 0 wrong  CORRECT
cost     6 steps · 10 model calls · 27,850 tokens read (12,566 cached) · 42s · est. API price $0.09
edits    3 accepted, 0 rejected, 1 held back, 3 warnings
```

---

### 5. Tidy-up turns don't count as task steps

[▶ Watch this step (12:26)](https://youtu.be/d-Pj4E4Z0ME?t=746)

**How it's implemented.** A task step is one harness turn: one batch, or the question (`run_turn`, line 296:
`if kind in ("batch", "question"): stats["steps"] += 1`). Claude's edit calls happen *inside* that turn's
single SDK `query()`, up to `--max-inner` model calls (`max_turns=...args.max_inner`, line 248). They
cost money, but they aren't steps.

**Run it.** The `cost` line of any self-editing run shows both numbers. In the full run (step 7):

```text
cost     13 steps · 25 model calls · 76,909 tokens read (47,536 cached) · 73s · est. API price $0.18
```

That's 13 steps (12 batches plus the question) but 25 model calls. Count the calls when you compare costs.

---

### 6. No read tool: one edit tool and nothing else

[▶ Watch this step (4:26)](https://youtu.be/d-Pj4E4Z0ME?t=266) · [guard rails (13:28)](https://youtu.be/d-Pj4E4Z0ME?t=808)

**How it's implemented.** The context is already in the prompt, so Claude never needs to read a file.
`call_model` (lines 236–250) turns off every built-in Claude Code tool and replaces the system prompt.
Three hooks (lines 209–224) enforce it:

```python
        system_prompt=sys_txt,        # replaces Claude Code's default system prompt
        tools=[],                     # no built-in tools: no Bash, no Read, no Write
        allowed_tools=[] if (final or no_edit) else [EDIT_TOOL],
        permission_mode="dontAsk",    # anything not pre-approved is denied
        setting_sources=[],           # ignore user/project settings and CLAUDE.md
```

- `PreToolUse` denies any tool except `edit_context`.
- `PreCompact` blocks Claude Code's own compaction, because the harness manages the context itself.
- The system prompt and the task line are pinned outside the editable file, so an edit can't remove them.

**Run it.** It's the same log check as step 3. Every batch turn offers only `['mcp__ctx__edit_context']`,
and `denied tool calls: 0` means Claude never tried anything else.

---

### 7. The comparison: drop oldest vs summary vs self-editing

[▶ Drop oldest (4:49)](https://youtu.be/d-Pj4E4Z0ME?t=289) · [Summary (5:03)](https://youtu.be/d-Pj4E4Z0ME?t=303) · [Self-editing (5:20)](https://youtu.be/d-Pj4E4Z0ME?t=320) · [Scoreboard (6:08)](https://youtu.be/d-Pj4E4Z0ME?t=368) · [One-sentence summary prompt (11:10)](https://youtu.be/d-Pj4E4Z0ME?t=670)

**How it's implemented.** One flag, the same task and the same budget (`run_turn`, lines 294–331):

| `--mode` | What happens when the context gets full | Code |
|---|---|---|
| `window` | the harness drops the oldest turns | `run_turn`, `turns.pop(0)` |
| `summary` | at 75% the harness asks for one summary that replaces the history | `run_turn`, `SUMMARY_ASK` |
| `clm` (default) | Claude edits its own context with `edit_context` | steps 1 to 6 |

The summary request is one sentence: *"Summarize the conversation so far in one message. Keep every fact
needed for the final question."*

**Run it.** These are full 12-batch runs, about 35 to 75 s each:

```bash
python ctx_loop_claude.py --mode window  --show --log runs/window.json
python ctx_loop_claude.py --mode summary --show --log runs/summary.json
python ctx_loop_claude.py --mode clm     --show --log runs/clm.json
```

**What to expect.** Window (5/14, 13 calls, ~36 s, ~$0.17):

```text
harness  window full: dropped the 2 oldest turn(s)  ~2,182 -> ~1,756 tokens
…
answer   ee1e8faf, cf269e1f, 90707ac6, 1066014b, c1cdcb4d
result   found 5/14 IDs, 0 wrong  WRONG
result   missed IDs came from batches [1, 2, 3, 4, 5, 6]
cost     13 steps · 13 model calls · 34,618 tokens read (0 cached) · 36s · est. API price $0.17
```

Summary (14/14, 17 calls, ~73 s, ~$0.21):

```text
harness  over 75% of budget: asking for a summary
harness  summary replaces the history: ~1,770 -> ~478 tokens
…
result   found 14/14 IDs, 0 wrong  CORRECT
cost     13 steps · 17 model calls · 35,688 tokens read (0 cached) · 73s · est. API price $0.21
```

Self-editing (14/14, 25 calls, ~73 s, ~$0.18):

```text
env      log batch 12/12 arrives (+390 tokens)
context  ███████████████████████████░░░░░░░ ~1,601 / 2,000
claude   calls edit_context:
           m = re.search(r"=== log batch 12/12 ===.*?(?=\n\[\[CTX_TURN|\Z)", s, flags=re.S)
           ids = re.findall(r"\[ERROR\] billing req=([0-9a-f]{8})", m.group(0))
           s = s.replace(m.group(0), "batch 12 done: billing ERROR ids: " + (", ".join(ids) or
             "none"))
harness  ✓ [LIVE_CTX.txt edit accepted: ~1601 -> ~1217/2000 tokens]
…
env      the question arrives: list every billing ERROR req ID (final turn, no tools)
answer   5387f613, 1b98fbe4, cc099a1e, f1b9ab7c, 6111b4b5, 78aa8105, 88ebd524, 8ccda80c,
         8b435ef0, ee1e8faf, cf269e1f, 90707ac6, 1066014b, c1cdcb4d
result   found 14/14 IDs, 0 wrong  CORRECT
cost     13 steps · 25 model calls · 76,909 tokens read (47,536 cached) · 73s · est. API price $0.18
edits    12 accepted, 0 rejected, 0 held back, 3 warnings
```

Re-score any log with `python score.py runs/clm.json`, which prints `found 14/14, 0 wrong -> CORRECT`. Try
`--seed 12` for a second task with 6 IDs.

---

### 8. The offline test

[▶ The size check in the video (8:44)](https://youtu.be/d-Pj4E4Z0ME?t=524)

**How it's implemented.** `test_offline.py` calls the `edit_context` handler directly on a 2-batch
context with six edits: one that grows the context, one that matches nothing, a good tidy-up, a Python
error, one that deletes every header, and one that empties the file. It needs no model calls and no login.

```bash
python test_offline.py
```

**What to expect** (about 1 s, free):

```text
PASS  adds text (grows)      [LIVE_CTX.txt edit rejected: it did not make the context smaller (~850 -> ~1000 tokens), s
PASS  matches nothing        [LIVE_CTX.txt unchanged: your code changed nothing (~850/2000 tokens)]
PASS  good tidy-up           [LIVE_CTX.txt edit accepted: ~850 -> ~468/2000 tokens]
PASS  python error           [LIVE_CTX.txt unchanged: your code changed nothing (~468/2000 tokens)]
PASS  deletes every header   [LIVE_CTX.txt edit accepted: ~468 -> ~458/2000 tokens]
PASS  empties the file       [LIVE_CTX.txt edit rejected: it would delete your whole context, so it was undone (~458 to
context after: [('user', 'READY\n\nbatch 1 done: billing ERROR ids: ')]
ALL PASS
```

When every header is deleted, the text survives as one user note, so nothing is lost.

---

### 9. Coding agents: an AGENTS.md card for Codex, plus an optional hook

[▶ Watch this step (10:08)](https://youtu.be/d-Pj4E4Z0ME?t=608) · [your app vs your coding agent (9:46)](https://youtu.be/d-Pj4E4Z0ME?t=586)

A coding agent keeps its own history, so you can't swap its context out each turn. Rules get you close.

- `agents-md/AGENTS.md`: the card we tested with Codex CLI on the same log task. It says to tidy after
  each batch, keep one `ANSWER-SO-FAR:` note, and treat the question as an answer-only final turn.
- `agents-md/NOTES_RULE.md`: a general running-note rule for everyday coding. Paste it into `CLAUDE.md`
  (Claude Code) or `AGENTS.md` (Codex).
- `agents-md/.codex/hooks.json` and `remind.py`: an optional Codex `UserPromptSubmit` hook. It adds the
  context size and, on the question turn, repeats the final-turn rule.

**Run it.** Copy the card and the hook into your project, then test the hook script on its own:

```bash
cp agents-md/AGENTS.md /path/to/your-repo/
cp -r agents-md/.codex /path/to/your-repo/
cd /path/to/your-repo
echo '{"prompt": "=== YOUR CONTEXT (~1346/2000 tokens) ===\n[[CTX_TURN 4 role=user]]\n=== log batch 2/12 ==="}' | python3 .codex/remind.py
echo '{"prompt": "=== YOUR CONTEXT (~980/2000 tokens) ===\n[[CTX_TURN 9 role=user]]\nQUESTION: List the request IDs"}' | python3 .codex/remind.py
```

**What to expect:**

```text
{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "[context ~1346/2000 tokens] New batch: tidy LIVE_CTX.txt now. Replace processed raw batches with notes; copy every ID forward."}}
{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "[context ~980/2000 tokens] This is the question: answer from your notes first, then raw lines. Don't edit or run commands."}}
```

Codex runs project hooks only in a trusted folder. The card is a rule, not a lock: in one of four Codex
runs the model misread a line and added two wrong IDs. See `agents-md/README.md` for the caveats.

---

## Options

| Flag | Default | What it does |
|---|---|---|
| `--mode` | `clm` | `window`, `summary` or `clm` |
| `--model`, `--effort` | `sonnet`, `low` | any model name the SDK accepts; `haiku` is cheaper but weaker at self-editing |
| `--budget` | `2000` | context budget in tokens (tiktoken `o200k_base`) |
| `--batches`, `--seed` | `12`, `11` | task size; seed 12 has 6 target IDs |
| `--show` | off | live view: budget bar, Claude's edit code, receipts |
| `--log FILE` | none | JSON log of every call (the folder is created if needed) |
| `--workdir DIR` | temp folder | keep `LIVE_CTX.txt` after the run |
| `--retries`, `--max-inner` | `3`, `6` | rollback attempts; model calls allowed inside one turn |
| `--no-edit-batches N` | `0` | testing: withhold the edit tool for the first N batches to force a rollback |
| `--show-warnings` | off | print the budget warning texts and exit (no model calls) |

## Results (Claude Sonnet, low effort, 7 Oct 2026)

| Mode | Seed 11 (14 IDs) | Seed 12 (6 IDs) | Model calls per run | Tokens read per run | Time per run | Est. API price per run |
|---|---|---|---|---|---|---|
| window | 5/14, 5/14, 5/14 | 3/6 | 13 | ~35K | ~35 s | ~$0.17 |
| summary | 14/14, 14/14, 14/14 | 6/6 | 16–17 | ~32–36K | ~50–75 s | ~$0.18–0.21 |
| clm (self-editing) | 14/14 × 4 | 6/6 | 23–25 | ~70–77K (about 60% cache reads) | ~60–75 s | ~$0.17–0.18 |

- **Window** always loses the earliest batches: every miss on seed 11 comes from batches 1 to 6.
- **Summary and self-editing tie** on accuracy. Self-editing makes about 50% more model calls and reads
  about twice the text, but most of that is cached re-reading, so the price comes out about the same.
- **Forced over-budget test** (step 4): one batch held back, the context shrank from ~1,781 to ~643 tokens,
  then 8/8.
- **Claude Haiku 4.5** at low effort scored 6/14 in self-editing mode. Some of its edits shrank the file
  but threw earlier notes away, and the size check can't tell.

On a short task where the model knows the question up front, a good summary is hard to beat. The paper's
gains are on long, open-ended tasks. [Measure on your own tasks](https://youtu.be/d-Pj4E4Z0ME?t=835) before
you switch. `examples/` has the logs of earlier runs made with this harness.

## How Meta's practices map to this code

| Practice (from the CLM paper) | Here | Where |
|---|---|---|
| The context is a file the model edits | Our code owns the history and renders it with `[[CTX_TURN i role=...]]` headers into `LIVE_CTX.txt`; every turn is one fresh SDK `query()` built from it | `render`, `parse`, `call_model` |
| Locate text with code, don't retype it | `edit_context(code)` runs the model's Python with `s` = the file text | `edit_context` |
| Edit gate plus a receipt | Kept only if the context got smaller; the tool returns "accepted ~old -> ~new" or "rejected" | `edit_context` |
| Don't lose the context to a bad parse | Header-less text is kept as a note; an edit that empties the file is refused | `parse`, `edit_context` |
| A running note, facts copied forward | One line of the system prompt | `CLM_RULES` |
| Tell the model its size, warn it early | Size in every header and receipt; warnings at 25/50/75% and an urgent one at 90%, via a `UserPromptSubmit` hook | `warning_for`, `hook_prompt` |
| Over budget: roll back and retry | The new batch is held back, the model gets a shrink-only turn, then the batch is delivered | `main` |
| Editing turns don't count as steps | Tool calls happen inside one query: 13 steps, about 25 model calls | `run_turn`, `call_model` |
| Final turn is answer-only | Separate system prompt, no tool server, one call | `call_model` |
| Pin the system prompt and task | They live outside the editable file | `PINNED_SYS`, `PINNED_TASK` |
| Only the edit tool | `tools=[]`; a `PreToolUse` hook denies anything else; a `PreCompact` hook blocks Claude Code's own compaction | `HOOKS` |

Not covered: the paper's reinforcement-learning training and its suffix-cache-reuse serving work.

## Caveats

- Small samples: one to four runs per mode and seed.
- The size check only measures size. It can't tell whether facts survived an edit.
- `edit_context` runs model-written Python on your machine (in the work folder, with a 30-second timeout).
  Fine for a demo; sandbox it in a real app.
- The SDK's default system prompt is replaced, but Claude Code still adds a one-line identity prefix.
- Budget token counts use tiktoken's `o200k_base`, not Claude's tokenizer. The budget is a harness rule,
  so a consistent count is what matters.

## Sources

- Context Language Models (University of Washington and Meta Superintelligence Labs, 2026):
  https://arxiv.org/abs/2609.37725
- Meta's official code (CC BY-NC 4.0): https://github.com/facebookresearch/context-language-models.
  This repository contains none of that code. It was an inspiration only.
- Learning from Research: Toward Lifelong Agent Harness Evolution (UC Santa Barbara and Microsoft, 2026):
  https://arxiv.org/abs/2609.40169
- Claude Agent SDK: [overview](https://code.claude.com/docs/en/agent-sdk/overview),
  [sessions](https://code.claude.com/docs/en/agent-sdk/sessions),
  [custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools),
  [hooks](https://code.claude.com/docs/en/agent-sdk/hooks),
  [Python SDK](https://github.com/anthropics/claude-agent-sdk-python)
- Codex CLI: [AGENTS.md](https://developers.openai.com/codex/guides/agents-md),
  [hooks](https://developers.openai.com/codex/hooks)

## Licence and credit

MIT, for the code in this repository (see `LICENSE`). Ideas credited to the Context Language Models
paper by the University of Washington and Meta Superintelligence Labs; the code here is our own. Made for
the Pro AI Clips video [Meta Context Language Models - Start Using it](https://youtu.be/d-Pj4E4Z0ME).

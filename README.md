# Let Claude edit its own context

A small, runnable harness that puts three context strategies head to head on the same task, using
**Claude Sonnet through the [Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/overview)**:

| Mode | What happens when the context gets full |
|---|---|
| `window` | the harness drops the oldest messages |
| `summary` | at 75% of the budget, the harness asks for one summary that replaces the history |
| `clm` | **the model edits its own context** with an `edit_context` tool |

The self-editing mode follows the ideas in Meta's *Context Language Models* paper: the context is a
file, the model rewrites it with code, a size check guards every edit, and the model is warned as the
budget fills up. Everything here is our own code, written for the Agent SDK. It is a demo of the ideas,
not a port of Meta's harness.

This is the code from the Pro AI Clips video *Let Your Model Edit Its Own Context*.

## The task

Twelve batches of synthetic server logs arrive one at a time (`logtask.py`). At the end the model must
list the request ID of every billing `[ERROR]` line: 14 IDs for seed 11, 6 for seed 12. The model knows
the question from the start. The logs add up to about 4,700 tokens against a **2,000-token budget**, so
something has to give. Scoring is an exact set match (`score.py`).

## Quick start

```bash
git clone https://github.com/schoolofai/claude-self-editing-context
cd claude-self-editing-context
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python test_offline.py                         # checks the edit tool; no model calls, no login
python ctx_loop_claude.py --batches 1 --show   # 1-batch smoke run (3 model calls)
python ctx_loop_claude.py --mode clm --show    # the full self-editing run
python ctx_loop_claude.py --mode summary --show
python ctx_loop_claude.py --mode window --show
```

**Login.** The Agent SDK runs the Claude Code CLI it ships with. It uses `ANTHROPIC_API_KEY` if that is
set; otherwise it uses your Claude Code login (run `claude` once and sign in). See the
[Agent SDK docs](https://code.claude.com/docs/en/agent-sdk/overview) for the authentication options
that apply to you.

**Model.** `--model sonnet` (default) and `--effort low` (default). `--model haiku` is cheaper, but in
our tests it was not good at self-editing (see results). Any model name the SDK accepts works.

**Cost.** A full run is 13 to 25 model calls. The SDK reports an estimated price at API rates: about
$0.17 to $0.21 per full run with Sonnet at low effort, and about $0.02 for the 1-batch smoke run. On a
Claude subscription it counts toward your plan's usage instead.

Other options: `--seed 12`, `--budget`, `--batches`, `--log run.json` (JSON log of every call),
`--workdir` (keep `LIVE_CTX.txt` after the run), `--no-edit-batches N` (testing: withhold the edit tool
for the first N batches to force the over-budget path). `python logtask.py` prints the logs and the
answer key; `python score.py run.json` re-scores a log.

## Results (Claude Sonnet, low effort, 7 Oct 2026)

| Mode | Seed 11 (14 IDs) | Seed 12 (6 IDs) | Model calls per run | Tokens read per run | Time per run | Est. API price per run |
|---|---|---|---|---|---|---|
| window | 5/14, 5/14 | 3/6 | 13 | ~35K | ~35 s | ~$0.17 |
| summary | 14/14, 14/14 | 6/6 | 16–17 | ~32–35K | ~50–65 s | ~$0.18–0.21 |
| clm (self-editing) | 14/14, 14/14, 14/14 | 6/6 | 23–25 | ~70–76K (about 60% cache reads) | ~60–75 s | ~$0.17–0.18 |

- **Window** always loses the earliest batches: the misses on seed 11 all come from batches 1 to 6.
- **Summary and self-editing tie** on accuracy. Self-editing makes about 50% more model calls (each
  tidy-up is an extra call inside the same turn) and reads about twice the text, but most of that is
  cached re-reading, so the estimated price comes out about the same.
- **Forced over-budget test** (5 batches, edit tool withheld for the first 3): one batch held back, the
  model shrank its context from ~1,782 to ~632 tokens, then found 8/8.
- **Claude Haiku 4.5** at low effort scored 6/14 in self-editing mode: some of its edits shrank the file
  but threw earlier notes away, and the size check can't tell.

Logs from the runs made with exactly this code are in `examples/` (seed 11: window, summary, clm, and
the forced test). The other runs used the same harness logic with earlier prompt wording.

On a short task where the model knows the question up front, a good summary is hard to beat. The
paper's gains are on long, open-ended tasks. Measure on your own tasks before you switch.

## How Meta's practices map to this code

| Practice (from the CLM paper) | Here | Where |
|---|---|---|
| The context is a file the model edits | Our code owns the history and renders it with `[[CTX_TURN i role=...]]` headers into `LIVE_CTX.txt`; every turn is one fresh SDK `query()` built from it | `render`, `parse`, `call_model` |
| Locate text with code, don't retype it | `edit_context(code)` runs the model's Python with `s` = the file text | `edit_context` |
| Edit gate plus a receipt | The edit is kept only if the context got smaller; the tool returns "accepted ~old -> ~new" or "rejected" | `edit_context` |
| Don't lose the context to a bad parse | Text left without a header is kept as a note; an edit that empties the file is refused | `parse`, `edit_context` |
| A running note, facts copied forward | One line of the system prompt | `CLM_RULES` |
| Tell the model its size, warn it early | Size in every prompt header and receipt; warnings at 25/50/75% and an urgent one at 90%, injected by a `UserPromptSubmit` hook | `warning_for`, `hook_prompt` |
| Over budget: roll back and retry | The new batch is held back, the model gets a shrink-only turn, then the batch is delivered | `main` |
| Editing turns don't count as steps | Tool calls happen inside one query: 13 steps, about 25 model calls | `call_model` (`max_turns`) |
| Final turn is answer-only | Separate system prompt, no tool server, `tools=[]`, one call | `call_model` |
| Pin the system prompt and task | They live outside the editable file | `PINNED_SYS`, `PINNED_TASK` |
| Only the edit tool, nothing else | `tools=[]` (no Bash/Read/Write), a `PreToolUse` hook denies anything else, a `PreCompact` hook blocks Claude Code's own compaction | `HOOKS` |

Not covered: the paper's reinforcement-learning training and its suffix-cache-reuse serving work.

## Coding agents (Codex CLI, Claude Code)

`agents-md/` has the `AGENTS.md` we tested with Codex CLI on the same task (tidy after each batch, keep
one running note, answer-only final turn), an optional Codex hook, and a general `NOTES.md` rule for
`CLAUDE.md` or `AGENTS.md`. See `agents-md/README.md` for the caveats: it's a rule, not a lock.

## Caveats

- Small samples: one to four runs per mode and seed.
- The size check only measures size. It can't tell whether facts survived an edit.
- `edit_context` runs model-written Python on your machine (in a temp folder, 30-second timeout).
  Fine for a demo; sandbox it in a real app.
- The SDK's own system prompt is replaced, but Claude Code still adds a one-line identity prefix.
- Token counts for the budget use tiktoken's `o200k_base`, not Claude's tokenizer. The budget is a
  harness rule, so a consistent count is what matters.

## Sources

- Context Language Models (University of Washington and Meta Superintelligence Labs, 2026):
  https://arxiv.org/abs/2609.37725
- Meta's official code (CC BY-NC 4.0): https://github.com/facebookresearch/context-language-models.
  This repository contains none of that code. It was an inspiration only.
- Learning from Research: Toward Lifelong Agent Harness Evolution (UC Santa Barbara and Microsoft, 2026):
  https://arxiv.org/abs/2609.40169
- Claude Agent SDK: https://code.claude.com/docs/en/agent-sdk/overview,
  [sessions](https://code.claude.com/docs/en/agent-sdk/sessions),
  [custom tools](https://code.claude.com/docs/en/agent-sdk/custom-tools),
  [hooks](https://code.claude.com/docs/en/agent-sdk/hooks),
  [Python SDK](https://github.com/anthropics/claude-agent-sdk-python)
- Codex CLI: [AGENTS.md](https://developers.openai.com/codex/guides/agents-md),
  [hooks](https://developers.openai.com/codex/hooks)

## License

MIT, for the code in this repository. See `LICENSE`.

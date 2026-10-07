# Getting close in a coding agent: AGENTS.md and CLAUDE.md

A coding agent such as Codex CLI or Claude Code keeps its own conversation history, so you can't
swap the context out each turn the way `ctx_loop_claude.py` does. You can get close with a few rules.

## `NOTES_RULE.md`: a running note for everyday coding work
Paste it into `CLAUDE.md` (Claude Code) or `AGENTS.md` (Codex). The agent keeps a short `NOTES.md`,
rewrites it after each milestone, and copies verified facts forward, so a fresh session can start
from the note instead of re-reading everything.

## `AGENTS.md`: the version we tested on the log-triage task with Codex CLI
The conditional version: tidy after every batch, keep one running note, and treat the question as an
answer-only final turn. In our tests on the same task as the main harness it obeyed the final-turn rule
in every run, with three caveats:

- **It's a rule, not a lock.** Nothing enforces it. In one of four runs the model misread a line and
  added two wrong IDs.
- **It costs more.** About a quarter more tokens than the harness, probably from re-reading the file.
- **Wording matters.** One version said "use your edited context as the reference", and the model
  refused to answer because it went looking for a file.

## `.codex/hooks.json` + `.codex/remind.py` (optional)
A `UserPromptSubmit` hook that adds the current context size and, on the question turn, repeats the
final-turn rule. It is written for the log-triage prompt format (it looks for `~N/M tokens` and
`QUESTION:`); adapt the two messages to your own work. Codex only runs project hooks in a trusted folder.

Docs: https://developers.openai.com/codex/guides/agents-md and https://developers.openai.com/codex/hooks

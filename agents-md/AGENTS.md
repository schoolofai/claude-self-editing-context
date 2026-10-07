# AGENTS.md

## Batch turns
- Your context is mirrored in LIVE_CTX.txt. Budget: 2000 tokens.
- After each batch, tidy LIVE_CTX.txt: replace processed raw batches with short notes.
- Keep ONE running note: "ANSWER-SO-FAR:" plus every ID so far. Never drop an ID.
- Edit with code; never retype text. Keep each turn's header.

## The question (final turn)
- When the newest message is a QUESTION, this is the final turn.
  Do NOT edit files or run commands.
- Answer from the context: every note and every raw line, notes first.

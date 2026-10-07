import json, re, sys
p = json.load(sys.stdin).get("prompt", "")
m = re.search(r"~(\d+)/(\d+) tokens", p)
size = f"[context ~{m[1]}/{m[2]} tokens] " if m else ""
if "QUESTION:" in p.rsplit("[[CTX_TURN", 1)[-1]:
    msg = "This is the question: answer from your notes first, then raw lines. Don't edit or run commands."
else:
    msg = "New batch: tidy LIVE_CTX.txt now. Replace processed raw batches with notes; copy every ID forward."
print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit",
                                         "additionalContext": size + msg}}))

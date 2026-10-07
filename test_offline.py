"""Offline check of the edit tool (size check, receipts, header handling). No model calls, no login needed.

    python test_offline.py
"""
import asyncio, os, shutil, sys, tempfile
sys.argv = ["ctx_loop_claude.py", "--batches", "2", "--workdir", tempfile.mkdtemp(prefix="ctx-test-")]
import ctx_loop_claude as H

H.turns.append({"role": "user", "content": H.task.batch_text(1)})
open(H.CTX, "w").write(H.render(H.turns))
edit = H.edit_context.handler

CASES = [
    ("adds text (grows)", "s = s + '\\nextra note ' * 50", "rejected"),
    ("matches nothing", "s = s.replace('no such text', 'x')", "unchanged"),
    ("good tidy-up", "m = re.search(r'=== log batch 1/2 ===.*?(?=\\n\\[\\[CTX_TURN|\\Z)', s, flags=re.S)\n"
                     "ids = re.findall(r'\\[ERROR\\] billing req=([0-9a-f]{8})', m.group(0))\n"
                     "s = s.replace(m.group(0), 'batch 1 done: billing ERROR ids: ' + ', '.join(ids))", "accepted"),
    ("python error", "s = s.nope()", "unchanged"),
    ("deletes every header", "s = re.sub(r'\\[\\[CTX_TURN[^\\]]*\\]\\]\\n', '', s)", "accepted"),
    ("empties the file", "s = ''", "rejected"),
]

async def main():
    ok = True
    H.turns.insert(0, {"role": "assistant", "content": "READY"}); open(H.CTX, "w").write(H.render(H.turns))
    for label, code, want in CASES:
        r = (await edit({"code": code}))["content"][0]["text"].splitlines()[-1]
        good = want in r
        ok &= good
        print(f"{'PASS' if good else 'FAIL'}  {label:<22} {r[:90]}")
    # after "deletes every header" the text survives as one user note, so nothing is lost
    assert any("batch 1 done" in t["content"] for t in H.turns), H.turns
    print("context after:", [(t["role"], t["content"][:40]) for t in H.turns])
    print("ALL PASS" if ok else "SOME CHECKS FAILED")
    return ok

ok = asyncio.run(main())
shutil.rmtree(H.WORK, ignore_errors=True)
sys.exit(0 if ok else 1)

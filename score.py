"""score.py: exact-set scoring for the log-triage task.

    python score.py --seed 11 --answer "5387f613, 1b98fbe4, ..."
    python score.py runs/clm-s11.json          # re-score a run log written with --log
"""
import json, re
from logtask import make_task

ID = re.compile(r"\b[0-9a-f]{8}\b")


def score(answer, truth, truth_by_batch):
    got, want = set(ID.findall(answer or "")), set(truth)
    found, missed, wrong = got & want, want - got, got - want
    missed_batches = sorted({b + 1 for b, ids in enumerate(truth_by_batch) for x in ids if x in missed})
    return dict(found=len(found), total=len(want), wrong=sorted(wrong), missed=sorted(missed),
                missed_batches=missed_batches, ok=bool(answer) and not missed and not wrong)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?", help="run log JSON written by ctx_loop_claude.py --log")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--batches", type=int, default=12)
    ap.add_argument("--answer", default=None)
    a = ap.parse_args()
    if a.log:
        d = json.load(open(a.log))
        r = score(d["answer"], d["truth"], d["truth_by_batch"])
    else:
        t = make_task(a.seed, a.batches)
        r = score(a.answer or "", t.truth, t.truth_by_batch)
    print(f"found {r['found']}/{r['total']}, {len(r['wrong'])} wrong -> {'CORRECT' if r['ok'] else 'WRONG'}")
    if r["missed_batches"]:
        print("missed IDs came from batches", r["missed_batches"])

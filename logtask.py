"""logtask.py: the synthetic log-triage task used in the demo.

Log lines arrive in batches. At the end the model must list the request IDs of every
billing [ERROR] line across all batches. With the default 2,000-token budget the raw logs
(about 4,700 tokens for 12 batches) cannot all fit, so something has to be dropped,
summarised or edited.

    from logtask import make_task
    task = make_task(seed=11, n_batches=12)   # 14 target IDs
    task = make_task(seed=12, n_batches=12)   # 6 target IDs
"""
import random
from dataclasses import dataclass, field

SERVICES = ["billing", "auth", "search", "db-proxy"]
LEVELS = ["INFO", "INFO", "WARN", "ERROR"]
WORDS = "timeout retry cache miss upstream reset queue full slow query token expired".split()
LINES_PER_BATCH = 14

QUESTION = ('QUESTION: List the request IDs (the req=... value) of EVERY [ERROR] line from service "billing", '
            'across ALL log batches. Reply with the IDs only, comma-separated.'
            ' Your notes and summaries count: use every note, summary and raw log line in the context above. '
            'List the IDs from notes or summaries first, then any from raw log lines.')


@dataclass
class Task:
    seed: int
    batches: list = field(default_factory=list)          # list of lists of log lines
    truth: list = field(default_factory=list)            # every target ID, in order
    truth_by_batch: list = field(default_factory=list)   # target IDs per batch

    def batch_text(self, i):
        """Message text for batch i (1-based)."""
        return f"=== log batch {i}/{len(self.batches)} ===\n" + "\n".join(self.batches[i - 1])


def make_task(seed=11, n_batches=12):
    rnd = random.Random(seed)
    t = Task(seed=seed)
    for b in range(n_batches):
        lines, ids = [], []
        for i in range(LINES_PER_BATCH):
            svc, lvl, rid = rnd.choice(SERVICES), rnd.choice(LEVELS), f"{rnd.getrandbits(32):08x}"
            if svc == "billing" and lvl == "ERROR":
                t.truth.append(rid); ids.append(rid)
            lines.append(f"2026-10-07T09:{b:02d}:{i*4:02d}Z [{lvl}] {svc} req={rid} {' '.join(rnd.sample(WORDS, 3))}")
        t.batches.append(lines); t.truth_by_batch.append(ids)
    return t


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Print the generated log batches and the answer key.")
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--batches", type=int, default=12)
    a = ap.parse_args()
    task = make_task(a.seed, a.batches)
    for i in range(1, len(task.batches) + 1):
        print(task.batch_text(i), "\n")
    print(f"answer key ({len(task.truth)} IDs):", ", ".join(task.truth))

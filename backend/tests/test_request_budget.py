from concurrent.futures import ThreadPoolExecutor

import pytest

from app.evaluation.request_budget import RequestBudget


def test_separate_budget_instances_share_limit_and_never_reset(tmp_path):
    path = tmp_path / "requests.json"
    first = RequestBudget(path, limit=3)
    first.reserve()
    second = RequestBudget(path, limit=3)
    second.reserve()
    first.reserve()
    assert RequestBudget(path, limit=3).used == 3
    with pytest.raises(RuntimeError, match="budget_exhausted"):
        second.reserve()


def test_concurrent_reservations_from_independent_instances_are_bounded(tmp_path):
    path = tmp_path / "requests.json"
    RequestBudget(path, limit=5)

    def attempt(_):
        try:
            RequestBudget(path, limit=5).reserve()
            return True
        except RuntimeError:
            return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(attempt, range(12))) == 5
    assert RequestBudget(path, limit=5).used == 5


def test_independent_processes_share_one_task_budget(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    path = tmp_path / "task-requests.json"
    RequestBudget(path, limit=7)
    script = """import sys
from app.evaluation.request_budget import RequestBudget
b=RequestBudget(sys.argv[1],limit=7)
count=0
for _ in range(4):
 try: b.reserve(); count+=1
 except RuntimeError: pass
print(count)
"""
    jobs = [
        subprocess.Popen(
            [sys.executable, "-c", script, str(path)],
            cwd=Path(__file__).resolve().parents[1],
            stdout=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    counts = [int(job.communicate(timeout=15)[0]) for job in jobs]
    assert all(job.returncode == 0 for job in jobs)
    assert sum(counts) == 7 and RequestBudget(path, limit=7).used == 7

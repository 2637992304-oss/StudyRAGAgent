"""Check exact cells, complete join keys and expected counts without an LLM."""

import argparse
import json
from pathlib import Path

from app.evaluation.table_checks import check_case


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument(
        "--checks", type=Path, help="Private {case_id: [row/count/join checks]}"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = json.loads((args.run / "cases.json").read_text(encoding="utf-8"))
    checks = json.loads(args.checks.read_text(encoding="utf-8")) if args.checks else {}
    results = [check_case(case, checks.get(case["id"])) for case in cases]
    summary = {
        key: sum(r["status"] == key for r in results)
        for key in ("pass", "fail", "not_applicable")
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "checks.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (args.output / "summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary))
    if summary["fail"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()

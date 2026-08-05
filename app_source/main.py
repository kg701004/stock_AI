"""Run a reproducible example of the configurable end-of-day decision engine."""

from pathlib import Path
import json
import sys

from input_adapter import load_factor_csv
from storage_paths import storage_paths
from weighted_analysis import assess_stock, assessment_as_dict, load_weight_config, persist_assessment


def main() -> None:
    # Windows consoles/redirects often default to a non-UTF-8 code page,
    # which turns the Chinese notes/warnings below into mojibake.
    sys.stdout.reconfigure(encoding="utf-8")
    config = load_weight_config(Path("config/analysis_weights.json"))
    decision_database = storage_paths()["decision_database"]
    input_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/sample_factor_scores.csv")
    for record in load_factor_csv(input_path):
        assessment = assess_stock(record, config)
        persist_assessment(decision_database, assessment)
        print(json.dumps(assessment_as_dict(assessment), ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()

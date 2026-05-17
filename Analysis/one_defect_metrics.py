import argparse
import json
import math
import re
from pathlib import PurePath


TECHNIQUES = ("tarantula", "ochiai", "op2", "barinel", "dstar")
TOP_K = (5, 10, 200)


def normalize_path(path):
    return path.replace("\\", "/").strip()


def path_matches(record_path, wanted_path):
    record = normalize_path(record_path).lower()
    wanted = normalize_path(wanted_path).lower()

    if "/" in wanted:
        return record == wanted or record.endswith("/" + wanted)

    return PurePath(record).name == wanted


def parse_faulty(values):
    faulty = []
    for value in values:
        if ":" not in value:
            raise ValueError(f"faulty line must be FILE:LINE, got {value!r}")
        file_name, line = value.rsplit(":", 1)
        faulty.append({"file": file_name, "line": int(line)})
    return faulty


def parse_faulty_file(path):
    values = []
    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            values.append(line)
    return parse_faulty(values)


def parse_fault_localization(path):
    records = []
    current = {}
    metric_re = re.compile(r"^(Tarantula|Ochiai|Op2|Barinel|Dstar)_(rank|exam) num: (.+)$")

    def flush():
        if "file" in current and "line" in current:
            records.append(current.copy())
        current.clear()

    with open(path, encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith("___________________"):
                flush()
                continue
            if line.startswith("File: "):
                current["file"] = line.split(": ", 1)[1]
                continue
            if line.startswith("Line: "):
                current["line"] = int(line.split(": ", 1)[1])
                continue
            if line.startswith("TOTAL_NUM_ALL: "):
                current["total_num_all"] = int(line.split(": ", 1)[1])
                continue
            match = metric_re.match(line)
            if match:
                technique = match.group(1).lower()
                kind = match.group(2)
                value = float(match.group(3))
                if kind == "rank" and value.is_integer():
                    value = int(value)
                current[f"{technique}_{kind}"] = value
    flush()
    return records


def find_record(records, faulty_line):
    matches = [
        record
        for record in records
        if record.get("line") == faulty_line["line"]
        and path_matches(record.get("file", ""), faulty_line["file"])
    ]
    if len(matches) > 1:
        matches.sort(key=lambda item: len(normalize_path(item["file"])))
    return matches[0] if matches else None


def aggregate(values):
    if not values:
        return {"best": None, "worst": None, "average": None}
    return {
        "best": min(values),
        "worst": max(values),
        "average": sum(values) / len(values),
    }


def top_flags(rank_summary):
    result = {}
    for case_name, rank in rank_summary.items():
        result[case_name] = {
            f"top_{k}": None if rank is None else rank <= k
            for k in TOP_K
        }
    return result


def compute_metrics(records, faulty):
    matched = []
    missing = []
    for item in faulty:
        record = find_record(records, item)
        if record is None:
            missing.append(item)
        else:
            matched.append({"faulty": item, "record": record})

    per_technique = {}
    for technique in TECHNIQUES:
        ranks = [item["record"][f"{technique}_rank"] for item in matched]
        exams = [item["record"][f"{technique}_exam"] for item in matched]
        rank_summary = aggregate(ranks)
        exam_summary = aggregate(exams)
        per_technique[technique] = {
            "rank": rank_summary,
            "exam": exam_summary,
            "top": top_flags(rank_summary),
        }

    total_values = [
        item["record"].get("total_num_all")
        for item in matched
        if item["record"].get("total_num_all") is not None
    ]
    total_num_all = total_values[0] if total_values else None
    if total_values and any(value != total_num_all for value in total_values):
        total_num_all = sorted(set(total_values))

    return {
        "total_records": len(records),
        "total_num_all": total_num_all,
        "faulty_count": len(faulty),
        "matched_count": len(matched),
        "missing_count": len(missing),
        "matched_faulty_lines": [
            {
                "wanted": item["faulty"],
                "matched_file": item["record"]["file"],
                "matched_line": item["record"]["line"],
            }
            for item in matched
        ],
        "missing_faulty_lines": missing,
        "metrics": per_technique,
    }


def replace_non_finite(value):
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, list):
        return [replace_non_finite(item) for item in value]
    if isinstance(value, dict):
        return {key: replace_non_finite(item) for key, item in value.items()}
    return value


def main():
    parser = argparse.ArgumentParser(
        description="Compute one-defect SBFL metrics from a BugsInPy fault localization file."
    )
    parser.add_argument("--fl", required=True, help="Path to *_fault_localization.txt")
    parser.add_argument(
        "--faulty",
        action="append",
        default=[],
        help="Faulty line as FILE:LINE. Can be passed multiple times.",
    )
    parser.add_argument(
        "--faulty-file",
        help="Text file with one FILE:LINE entry per line.",
    )
    args = parser.parse_args()

    faulty = parse_faulty(args.faulty)
    if args.faulty_file:
        faulty.extend(parse_faulty_file(args.faulty_file))
    if not faulty:
        raise SystemExit("Pass at least one --faulty FILE:LINE or --faulty-file.")

    records = parse_fault_localization(args.fl)
    result = compute_metrics(records, faulty)
    print(json.dumps(replace_non_finite(result), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

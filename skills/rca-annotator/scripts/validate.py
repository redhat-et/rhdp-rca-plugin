#!/usr/bin/env python3
"""
Validation script for annotation.json files.

Reads enum constraints and structural rules directly from schemas/schema.json,
then validates that annotation.json conforms to them.

Usage:
    python scripts/validate.py --job-id <job_id>
"""

import argparse
import json
import sys
from pathlib import Path

SCHEMA_PATH = Path(__file__).parent.parent / "schemas" / "schema.json"

# Fields that must be present in every annotation
REQUIRED_FIELDS = ["job_id", "annotated_at", "root_cause", "evidence"]


def parse_enums(schema_obj: dict, path: str = "") -> dict[str, list[str]]:
    """
    Walk the schema and extract enum constraints from pipe-delimited strings.

    Pipe-delimited strings in schema.json (e.g. "high | medium | low") indicate
    the set of valid values for that field. This function recursively walks the
    schema and returns a map of dot-notation field paths to their valid values.

    Array item schemas are keyed with a "[]" suffix, e.g. "evidence[].source".
    """
    enums = {}
    for key, value in schema_obj.items():
        field_path = f"{path}.{key}" if path else key
        if isinstance(value, str) and " | " in value:
            enums[field_path] = [v.strip() for v in value.split(" | ")]
        elif isinstance(value, dict):
            enums.update(parse_enums(value, field_path))
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            enums.update(parse_enums(value[0], f"{field_path}[]"))
    return enums


def load_schema(schema_path: Path) -> tuple[dict, dict[str, list[str]]]:
    """Load schema.json and extract enum constraints."""
    with open(schema_path) as f:
        schema = json.load(f)
    return schema, parse_enums(schema)


def check_enum(value: str, field_path: str, enums: dict[str, list[str]], errors: list[str]) -> None:
    """Append an error if value is not in the enum set for field_path."""
    if field_path in enums and value not in enums[field_path]:
        valid = ", ".join(enums[field_path])
        errors.append(f"Invalid value for '{field_path}': '{value}'. Must be one of: {valid}")


def validate_annotation(
    annotation_path: Path, schema_path: Path = SCHEMA_PATH
) -> tuple[bool, list[str]]:
    """
    Validate annotation.json against schemas/schema.json.

    Enum constraints are read directly from the schema file so this validator
    stays in sync with schema.json automatically.

    Returns:
        (is_valid, error_messages)
    """
    errors = []

    if not annotation_path.exists():
        return False, [f"File not found: {annotation_path}"]

    try:
        with open(annotation_path) as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        return False, [f"Invalid JSON: {e}"]

    if not schema_path.exists():
        return False, [f"Schema not found: {schema_path}"]

    try:
        _schema, enums = load_schema(schema_path)
    except (json.JSONDecodeError, OSError) as e:
        return False, [f"Could not load schema: {e}"]

    # Required top-level fields
    for field in REQUIRED_FIELDS:
        if field not in data:
            errors.append(f"Missing required field: '{field}'")

    # root_cause
    if "root_cause" in data:
        rc = data["root_cause"]
        if not isinstance(rc, dict):
            errors.append("'root_cause' must be an object")
        else:
            if "summary" not in rc:
                errors.append("root_cause missing required field: 'summary'")
            if "category" in rc:
                check_enum(rc["category"], "root_cause.category", enums, errors)
            if "confidence" in rc:
                check_enum(rc["confidence"], "root_cause.confidence", enums, errors)

    # evidence
    if "evidence" in data:
        if not isinstance(data["evidence"], list):
            errors.append("'evidence' must be an array")
        else:
            root_cause_count = sum(1 for e in data["evidence"] if e.get("is_root_cause") is True)
            if root_cause_count == 0:
                errors.append("No evidence marked with 'is_root_cause: true'")
            elif root_cause_count > 1:
                errors.append(
                    f"Multiple evidence items marked as root cause ({root_cause_count}). Expected exactly 1."
                )

            for i, evidence in enumerate(data["evidence"]):
                if not isinstance(evidence, dict):
                    errors.append(f"evidence[{i}] must be an object")
                    continue
                if "source" not in evidence:
                    errors.append(f"evidence[{i}] missing 'source' field")
                elif "source" in evidence:
                    check_enum(evidence["source"], "evidence[].source", enums, errors)
                if "confidence" in evidence:
                    check_enum(evidence["confidence"], "evidence[].confidence", enums, errors)

    # difficulty
    if "difficulty" in data:
        check_enum(data["difficulty"], "difficulty", enums, errors)

    if "difficulty_score" in data:
        score = data["difficulty_score"]
        if not isinstance(score, (int, float)) or score < 0 or score > 10:
            errors.append(f"difficulty_score must be a number between 0 and 10, got: {score}")

    # alternative_diagnoses
    if "alternative_diagnoses" in data:
        if not isinstance(data["alternative_diagnoses"], list):
            errors.append("'alternative_diagnoses' must be an array")
        else:
            for i, alt in enumerate(data["alternative_diagnoses"]):
                if not isinstance(alt, dict):
                    errors.append(f"alternative_diagnoses[{i}] must be an object")
                    continue
                if "plausibility" in alt:
                    check_enum(
                        alt["plausibility"], "alternative_diagnoses[].plausibility", enums, errors
                    )

    # recommendations
    if "recommendations" in data:
        if not isinstance(data["recommendations"], list):
            errors.append("'recommendations' must be an array")
        else:
            for rec in data["recommendations"]:
                if "priority" in rec:
                    check_enum(rec["priority"], "recommendations[].priority", enums, errors)

    return len(errors) == 0, errors


def main():
    parser = argparse.ArgumentParser(
        description="Validate annotation.json against schemas/schema.json",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--job-id",
        required=True,
        help="Job ID to validate annotation for",
    )

    args = parser.parse_args()
    job_id = args.job_id

    annotation_path = Path(f".analysis/{job_id}/annotation.json")

    print(f"Validating annotation for job {job_id}...")
    print(f"Annotation: {annotation_path}")
    print(f"Schema:     {SCHEMA_PATH}")
    print()

    is_valid, errors = validate_annotation(annotation_path)

    if is_valid:
        print("Validation passed")
        print("   annotation.json is valid and ready to upload")
        return 0
    else:
        print("Validation failed!")
        print()
        print("Errors found:")
        for i, error in enumerate(errors, 1):
            print(f"  {i}. {error}")
        print()
        print("Please fix these errors before uploading.")
        return 1


if __name__ == "__main__":
    sys.exit(main())

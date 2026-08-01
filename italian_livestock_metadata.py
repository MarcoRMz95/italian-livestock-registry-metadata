#!/usr/bin/env python3
"""Retrieve public livestock metadata from Italy's VetInfo registry."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlencode

from playwright.sync_api import (
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)


REGISTRY_URL = (
    "https://www.vetinfo.it/sso_portale/informazioni/int_capi_no_log.pl"
)
SPECIES_CATEGORIES = {
    "cattle": "BOV",
    "sheep-goats": "OVI",
    "pigs": "SUI",
    "equids": "EQUI",
}
EQUID_ID_FIELDS = {
    "electronic": "P_CODICE_CAPO",
    "ueln": "P_CODICE_UELN",
    "passport": "P_PASSAPORTO",
}
MOVEMENT_FIELDS = [
    "query_code",
    "species",
    "movement_index",
    "movement_type",
    "establishment_code",
    "movement_date",
    "movement_reason",
]
OUTPUT_FIELDS = [
    "query_code",
    "query_code_type",
    "found",
    "species",
    "animal_code",
    "electronic_id",
    "ueln",
    "passport",
    "equid_name",
    "dpa",
    "sex",
    "breed",
    "birth_date",
    "movement_count",
    "final_event",
    "event_date",
    "event_location",
    "error",
]
IDENTIFIER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9./-]{2,39}$")
DATE_PATTERN = r"[0-9]{2}/[0-9]{2}/[0-9]{4}"


def normalize_text(text: str | None) -> str:
    """Normalize whitespace while preserving meaningful line boundaries."""
    if not text:
        return ""

    normalized = text.replace("\xa0", " ").replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n")]
    return "\n".join(line for line in lines if line)


def normalize_identifier(value: Any) -> str:
    """Return a safe, normalized animal identifier."""
    identifier = str(value).strip().upper()
    if not IDENTIFIER_PATTERN.fullmatch(identifier):
        raise ValueError(
            "Animal identifiers must contain 3-40 letters, digits, dots, "
            "slashes, or hyphens, with no spaces."
        )
    return identifier


def _extract(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE)
    return normalize_text(match.group(1)) if match else ""


def parse_movements(
    text: str | None,
    query_code: str,
    species: str,
) -> list[dict[str, Any]]:
    """Parse the public movement table into one record per establishment entry."""
    normalized = normalize_text(text)
    section_match = re.search(
        r"(?:^|\n)Entrato nello stabilimento(?:\n|\s+)"
        r"In data(?:\n|\s+)Motivo\n(?P<rows>.*)",
        normalized,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not section_match:
        return []

    row_pattern = re.compile(
        rf"^(?P<establishment>[A-Z0-9*./-]{{3,40}})(?:\n|\s+)"
        rf"(?P<date>{DATE_PATTERN})(?:\n|\s+)"
        r"(?P<reason>[^\n]+)$",
        flags=re.IGNORECASE | re.MULTILINE,
    )
    movements: list[dict[str, Any]] = []
    for index, match in enumerate(
        row_pattern.finditer(section_match.group("rows")),
        start=1,
    ):
        movements.append(
            {
                "query_code": query_code,
                "species": species,
                "movement_index": index,
                "movement_type": "entry",
                "establishment_code": normalize_text(match.group("establishment")),
                "movement_date": match.group("date"),
                "movement_reason": normalize_text(match.group("reason")),
            }
        )
    return movements


def parse_animal_metadata(
    text: str | None,
    query_code: str,
    species: str,
    query_code_type: str = "animal-id",
) -> dict[str, Any]:
    """Parse the basic animal fields shown by the public VetInfo page."""
    normalized = normalize_text(text)
    animal_code = _extract(normalized, r"Codice\s+Capo\s*:\s*([A-Z0-9./-]+)")
    birth_date = _extract(
        normalized,
        rf"Data\s+(?:di\s+)?nascita\s*:\s*({DATE_PATTERN})",
    )
    sex = _extract(normalized, r"Sesso\s*:\s*(.+?)(?=\s+Dpa\s*:|\n|$)")
    breed = _extract(
        normalized,
        r"Razza\s*:\s*(.+?)(?=\s+Identificativo/Nome|\n|$)",
    )
    ueln = _extract(normalized, r"Codice\s+UELN\s*:\s*([A-Z0-9./-]+)")
    passport = _extract(normalized, r"Passaporto\s*:\s*([A-Z0-9./-]+)")
    equid_name = _extract(normalized, r"Identificativo/Nome\s*:?\s*([^\n]+)")
    dpa = _extract(normalized, r"Dpa\s*:\s*([^\n]+)")
    movements = parse_movements(normalized, query_code, species)

    final_match = re.search(
        rf"(MACELLAZIONE\s+EFFETTUATA\s+IL\s+({DATE_PATTERN})\s+IN\s+([^\n]+))",
        normalized,
        flags=re.IGNORECASE,
    )
    final_event = normalize_text(final_match.group(1)) if final_match else ""
    event_date = final_match.group(2) if final_match else ""
    event_location = normalize_text(final_match.group(3)) if final_match else ""
    found = bool(animal_code)

    return {
        "query_code": query_code,
        "query_code_type": query_code_type,
        "found": found,
        "species": species,
        "animal_code": animal_code,
        "electronic_id": animal_code if species == "equids" else "",
        "ueln": ueln,
        "passport": passport,
        "equid_name": equid_name,
        "dpa": dpa,
        "sex": sex,
        "breed": breed,
        "birth_date": birth_date,
        "movement_count": len(movements),
        "movements": movements,
        "final_event": final_event,
        "event_date": event_date,
        "event_location": event_location,
        "error": "" if found else "No animal record was recognized in the response.",
    }


def build_registry_url(species: str) -> str:
    """Build the public registry URL for a supported species category."""
    try:
        portal_category = SPECIES_CATEGORIES[species]
    except KeyError as exc:
        supported = ", ".join(SPECIES_CATEGORIES)
        raise ValueError(f"Unsupported species category. Choose one of: {supported}") from exc
    return f"{REGISTRY_URL}?{urlencode({'P_CAPI': portal_category})}"


def read_codes(
    positional_codes: Iterable[str],
    input_csv: Path | None = None,
) -> list[str]:
    """Read and de-duplicate identifiers from CLI arguments and a CSV file."""
    raw_codes = list(positional_codes)

    if input_csv is not None:
        with input_csv.open("r", newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames or "animal_code" not in reader.fieldnames:
                raise ValueError("Input CSV must contain an 'animal_code' column.")
            raw_codes.extend(row.get("animal_code", "") for row in reader)

    codes: list[str] = []
    seen: set[str] = set()
    for raw_code in raw_codes:
        code = normalize_identifier(raw_code)
        if code not in seen:
            seen.add(code)
            codes.append(code)
    return codes


def equid_id_candidates(code: str, requested_type: str = "auto") -> list[str]:
    """Return likely equid fields, including fallbacks for ambiguous codes."""
    identifier = normalize_identifier(code)
    if requested_type != "auto":
        if requested_type not in EQUID_ID_FIELDS:
            choices = ", ".join(("auto", *EQUID_ID_FIELDS))
            raise ValueError(
                f"Unsupported equid identifier type. Choose one of: {choices}"
            )
        return [requested_type]

    # A 15-digit value can be either an electronic identifier or a numeric
    # UELN, so both public fields must be tried before declaring it missing.
    if len(identifier) == 15 and identifier.isdigit():
        return ["electronic", "ueln"]
    if len(identifier) == 15 and identifier.isalnum():
        return ["ueln"]
    return ["passport"]


def infer_equid_id_type(code: str) -> str:
    """Return the first likely equid identifier type for compatibility."""
    return equid_id_candidates(code)[0]


def resolve_equid_id_type(code: str, requested_type: str) -> str:
    """Resolve an explicit or automatically inferred equid identifier type."""
    return equid_id_candidates(code, requested_type)[0]


def auto_query_candidates(code: str, equid_id_type: str = "auto") -> list[tuple[str, str]]:
    """Return a deterministic lookup order for automatic species detection."""
    identifier = normalize_identifier(code)
    equid_candidates = [
        ("equids", code_type)
        for code_type in equid_id_candidates(identifier, equid_id_type)
    ]
    standard_candidates = [
        ("cattle", "animal-id"),
        ("sheep-goats", "animal-id"),
        ("pigs", "animal-id"),
    ]

    # Fifteen-character equid identifiers and short passport numbers are
    # distinctive enough to query the equid form first.
    likely_equid = (
        equid_id_type != "auto"
        or len(identifier) == 15
        or len(identifier) <= 10
    )
    if likely_equid:
        return [*equid_candidates, *standard_candidates]
    return [*standard_candidates, *equid_candidates]


def verify_registry_form(page: Page, species: str, timeout_ms: int) -> None:
    """Verify that a public VetInfo query form is available."""
    page.goto(
        build_registry_url(species),
        wait_until="domcontentloaded",
        timeout=timeout_ms,
    )

    search_button = page.locator("#CERCA_CAPI")
    category_input = page.locator('input[name="P_CAPI"]')

    expected_fields = (
        tuple(EQUID_ID_FIELDS.values())
        if species == "equids"
        else ("P_CODICE_CAPO",)
    )
    fields_present = all(
        page.locator(f'input[name="{field_name}"]').count() == 1
        for field_name in expected_fields
    )
    if (
        not fields_present
        or search_button.count() != 1
        or category_input.count() != 1
    ):
        raise RuntimeError("The expected VetInfo query form was not found.")

    expected_category = SPECIES_CATEGORIES[species]
    actual_category = category_input.get_attribute("value")
    if actual_category != expected_category:
        raise RuntimeError(
            f"Unexpected VetInfo category: expected {expected_category}, "
            f"received {actual_category!r}."
        )


def query_animal(
    page: Page,
    code: str,
    species: str,
    timeout_ms: int,
    settle_ms: int,
    equid_id_type: str = "auto",
) -> dict[str, Any]:
    """Submit one animal identifier and return parsed public metadata."""
    if species == "equids" and equid_id_type == "auto":
        return query_equid_auto(page, code, timeout_ms, settle_ms)

    try:
        verify_registry_form(page, species, timeout_ms)
        query_code_type = (
            resolve_equid_id_type(code, equid_id_type)
            if species == "equids"
            else "animal-id"
        )
        field_name = (
            EQUID_ID_FIELDS[query_code_type]
            if species == "equids"
            else "P_CODICE_CAPO"
        )
        code_input = page.locator(f'input[name="{field_name}"]')
        search_button = page.locator("#CERCA_CAPI")

        code_input.fill(code)
        search_button.click()
        page.wait_for_timeout(settle_ms)
        response_text = page.locator("body").inner_text(timeout=timeout_ms)
        return parse_animal_metadata(
            response_text,
            code,
            species,
            query_code_type=query_code_type,
        )
    except PlaywrightTimeoutError as exc:
        error = f"VetInfo request timed out: {exc}"
    except Exception as exc:
        error = str(exc)

    return {
        "query_code": code,
        "query_code_type": (
            resolve_equid_id_type(code, equid_id_type)
            if species == "equids"
            else "animal-id"
        ),
        "found": False,
        "species": species,
        "animal_code": "",
        "electronic_id": "",
        "ueln": "",
        "passport": "",
        "equid_name": "",
        "dpa": "",
        "sex": "",
        "breed": "",
        "birth_date": "",
        "movement_count": 0,
        "movements": [],
        "final_event": "",
        "event_date": "",
        "event_location": "",
        "error": error,
    }


def query_equid_auto(
    page: Page,
    code: str,
    timeout_ms: int,
    settle_ms: int,
) -> dict[str, Any]:
    """Try every plausible equid field until a matching record is found."""
    last_result: dict[str, Any] | None = None
    for code_type in equid_id_candidates(code):
        result = query_animal(
            page,
            code,
            "equids",
            timeout_ms,
            settle_ms,
            equid_id_type=code_type,
        )
        if result["found"]:
            return result
        last_result = result

    if last_result is None:
        raise RuntimeError("No equid identifier fields were available for the query.")
    return last_result


def detect_and_query_animal(
    page: Page,
    code: str,
    timeout_ms: int,
    settle_ms: int,
    equid_id_type: str = "auto",
) -> dict[str, Any]:
    """Query supported categories until VetInfo returns a recognized record."""
    for species, code_type in auto_query_candidates(code, equid_id_type):
        result = query_animal(
            page,
            code,
            species,
            timeout_ms,
            settle_ms,
            equid_id_type=code_type,
        )
        if result["found"]:
            return result

    return {
        "query_code": code,
        "query_code_type": "",
        "found": False,
        "species": "",
        "animal_code": "",
        "electronic_id": "",
        "ueln": "",
        "passport": "",
        "equid_name": "",
        "dpa": "",
        "sex": "",
        "breed": "",
        "birth_date": "",
        "movement_count": 0,
        "movements": [],
        "final_event": "",
        "event_date": "",
        "event_location": "",
        "error": "No record was found in any supported public category.",
    }


def write_results(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write query results to a UTF-8 CSV file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def derive_movements_path(metadata_path: Path) -> Path:
    """Derive the default movement CSV path from the metadata output path."""
    suffix = metadata_path.suffix or ".csv"
    return metadata_path.with_name(f"{metadata_path.stem}_movements{suffix}")


def write_movement_results(path: Path, rows: list[dict[str, Any]]) -> None:
    """Write one public movement record per CSV row."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=MOVEMENT_FIELDS,
            extrasaction="ignore",
        )
        writer.writeheader()
        writer.writerows(rows)


def run_smoke_test(headless: bool, timeout_ms: int) -> int:
    """Check all public forms without submitting an animal identifier."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=headless)
        page = browser.new_page()
        try:
            for species in SPECIES_CATEGORIES:
                verify_registry_form(page, species, timeout_ms)
                print(f"PASS: {species}")
        finally:
            browser.close()
    return 0


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Retrieve basic public livestock metadata from Italy's VetInfo "
            "BDN registry."
        )
    )
    parser.add_argument(
        "codes",
        nargs="*",
        help="Animal identifiers to query. No identifier is stored by this project.",
    )
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        help="Optional UTF-8 CSV file containing an 'animal_code' column.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("animal_metadata.csv"),
        help="Output CSV path (default: animal_metadata.csv).",
    )
    parser.add_argument(
        "--movements-output",
        type=Path,
        help=(
            "Movement CSV path (default: '<output stem>_movements.csv')."
        ),
    )
    parser.add_argument(
        "--species",
        choices=("auto", *SPECIES_CATEGORIES),
        default="auto",
        help="VetInfo query category (default: auto-detect).",
    )
    parser.add_argument(
        "--equid-id-type",
        choices=("auto", *EQUID_ID_FIELDS),
        default="auto",
        help=(
            "Equid identifier field: electronic, ueln, passport, or automatic "
            "format detection (default: auto)."
        ),
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Show the Chromium window instead of running headlessly.",
    )
    parser.add_argument(
        "--delay-seconds",
        type=float,
        default=1.5,
        help="Delay between requests; values below 1 second are rejected.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=30.0,
        help="Per-page timeout in seconds (default: 30).",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="Check all public forms without submitting an animal identifier.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_argument_parser()
    args = parser.parse_args(argv)

    if args.delay_seconds < 1.0:
        parser.error("--delay-seconds must be at least 1.0")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be greater than zero")

    timeout_ms = int(args.timeout_seconds * 1000)
    if args.smoke_test:
        return run_smoke_test(headless=not args.headed, timeout_ms=timeout_ms)

    try:
        codes = read_codes(args.codes, args.input)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    if not codes:
        parser.error("Provide at least one animal identifier or an input CSV.")

    results: list[dict[str, Any]] = []
    settle_ms = min(max(int(args.delay_seconds * 1000), 1000), 10000)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not args.headed)
        context = browser.new_context(locale="it-IT")
        page = context.new_page()
        try:
            for index, code in enumerate(codes, start=1):
                print(f"Querying animal {index}/{len(codes)}...")
                if args.species == "auto":
                    result = detect_and_query_animal(
                        page,
                        code,
                        timeout_ms,
                        settle_ms,
                        equid_id_type=args.equid_id_type,
                    )
                else:
                    result = query_animal(
                        page,
                        code,
                        args.species,
                        timeout_ms,
                        settle_ms,
                        equid_id_type=args.equid_id_type,
                    )
                results.append(result)
                if index < len(codes):
                    time.sleep(args.delay_seconds)
        finally:
            browser.close()

    write_results(args.output, results)
    movements = [
        movement
        for result in results
        for movement in result.get("movements", [])
    ]
    movements_output = args.movements_output or derive_movements_path(args.output)
    write_movement_results(movements_output, movements)
    found_count = sum(bool(row["found"]) for row in results)
    print(f"Saved {len(results)} rows to {args.output}")
    print(f"Saved {len(movements)} movements to {movements_output}")
    print(f"Records recognized: {found_count}/{len(results)}")
    return 0 if found_count == len(results) else 2


if __name__ == "__main__":
    sys.exit(main())

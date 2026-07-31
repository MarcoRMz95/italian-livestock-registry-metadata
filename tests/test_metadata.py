from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

from italian_livestock_metadata import (
    EQUID_ID_FIELDS,
    OUTPUT_FIELDS,
    SPECIES_CATEGORIES,
    auto_query_candidates,
    build_registry_url,
    infer_equid_id_type,
    normalize_identifier,
    parse_animal_metadata,
    read_codes,
    write_results,
)


SYNTHETIC_RESPONSE = """
Dati anagrafici
Codice Capo:
TEST123456789
Data nascita:
01/02/2020
Sesso:
FEMMINA
Razza:
SAMPLE BREED (TEST)
Movimentazioni
MACELLAZIONE EFFETTUATA IL 03/04/2025 IN SAMPLE LOCATION (XX)
"""

SYNTHETIC_EQUID_RESPONSE = """
Dati anagrafici
Codice Capo:
123456789012345
Data nascita:
01/02/2020
Codice UELN:
123456789AB1234
Passaporto:
TESTP1
Sesso:
MASCHIO
Dpa:
NO
Razza:
SAMPLE EQUID BREED
Identificativo/Nome
SAMPLE HORSE
"""


class ParsingTests(unittest.TestCase):
    def test_parses_basic_metadata_from_synthetic_response(self) -> None:
        row = parse_animal_metadata(
            SYNTHETIC_RESPONSE,
            query_code="TEST123456789",
            species="cattle",
        )

        self.assertTrue(row["found"])
        self.assertEqual(row["animal_code"], "TEST123456789")
        self.assertEqual(row["birth_date"], "01/02/2020")
        self.assertEqual(row["sex"], "FEMMINA")
        self.assertEqual(row["breed"], "SAMPLE BREED (TEST)")
        self.assertEqual(row["event_date"], "03/04/2025")
        self.assertEqual(row["event_location"], "SAMPLE LOCATION (XX)")
        self.assertNotIn("raw_text", row)

    def test_returns_not_found_for_a_form_without_a_record(self) -> None:
        row = parse_animal_metadata(
            "In questa sezione e possibile interrogare i capi.",
            query_code="TEST123",
            species="cattle",
        )

        self.assertFalse(row["found"])
        self.assertEqual(row["animal_code"], "")
        self.assertIn("No animal record", row["error"])

    def test_parses_equid_metadata_and_query_code_type(self) -> None:
        row = parse_animal_metadata(
            SYNTHETIC_EQUID_RESPONSE,
            query_code="TESTP1",
            species="equids",
            query_code_type="passport",
        )

        self.assertTrue(row["found"])
        self.assertEqual(row["query_code_type"], "passport")
        self.assertEqual(row["electronic_id"], "123456789012345")
        self.assertEqual(row["ueln"], "123456789AB1234")
        self.assertEqual(row["passport"], "TESTP1")
        self.assertEqual(row["equid_name"], "SAMPLE HORSE")
        self.assertEqual(row["dpa"], "NO")
        self.assertEqual(row["sex"], "MASCHIO")
        self.assertEqual(row["breed"], "SAMPLE EQUID BREED")


class InputTests(unittest.TestCase):
    def test_normalizes_and_validates_identifiers(self) -> None:
        self.assertEqual(normalize_identifier(" test-123 "), "TEST-123")
        with self.assertRaises(ValueError):
            normalize_identifier("invalid value with spaces")

    def test_reads_csv_and_deduplicates_codes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.csv"
            path.write_text(
                "animal_code\nTEST123\nTEST456\nTEST123\n",
                encoding="utf-8",
            )

            codes = read_codes(["TEST000"], path)

        self.assertEqual(codes, ["TEST000", "TEST123", "TEST456"])

    def test_rejects_csv_without_required_column(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "input.csv"
            path.write_text("wrong_column\nTEST123\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                read_codes([], path)


class OutputAndRoutingTests(unittest.TestCase):
    def test_builds_every_supported_species_url(self) -> None:
        for species, portal_code in SPECIES_CATEGORIES.items():
            with self.subTest(species=species):
                url = build_registry_url(species)
                self.assertIn(f"P_CAPI={portal_code}", url)

        self.assertEqual(SPECIES_CATEGORIES["equids"], "EQUI")

    def test_maps_all_three_equid_identifier_fields(self) -> None:
        self.assertEqual(
            EQUID_ID_FIELDS,
            {
                "electronic": "P_CODICE_CAPO",
                "ueln": "P_CODICE_UELN",
                "passport": "P_PASSAPORTO",
            },
        )

    def test_infers_equid_identifier_types(self) -> None:
        self.assertEqual(infer_equid_id_type("123456789012345"), "electronic")
        self.assertEqual(infer_equid_id_type("123456789AB1234"), "ueln")
        self.assertEqual(infer_equid_id_type("TESTP1"), "passport")

    def test_prioritizes_likely_or_explicit_equid_identifiers(self) -> None:
        self.assertEqual(
            auto_query_candidates("123456789012345")[0],
            ("equids", "electronic"),
        )
        self.assertEqual(
            auto_query_candidates("TESTP1")[0],
            ("equids", "passport"),
        )
        self.assertEqual(
            auto_query_candidates("IT000000000001")[0],
            ("cattle", "animal-id"),
        )
        self.assertEqual(
            auto_query_candidates("TEST-EQUID-01", "ueln")[0],
            ("equids", "ueln"),
        )

    def test_writes_only_documented_output_fields(self) -> None:
        row = parse_animal_metadata(
            SYNTHETIC_RESPONSE,
            query_code="TEST123456789",
            species="cattle",
        )
        row["unexpected"] = "not written"

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "output.csv"
            write_results(path, [row])
            with path.open("r", newline="", encoding="utf-8-sig") as handle:
                reader = csv.DictReader(handle)
                output_row = next(reader)

        self.assertEqual(reader.fieldnames, OUTPUT_FIELDS)
        self.assertNotIn("unexpected", output_row)


if __name__ == "__main__":
    unittest.main()

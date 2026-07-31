# Italian Livestock Registry Metadata

A small command-line tool that retrieves basic animal metadata from the public
query pages of Italy's VetInfo National Livestock Database (BDN).

The project is independent and is not affiliated with VetInfo, the Italian
Ministry of Health, or IZS Teramo.

## Features

- Queries the public VetInfo animal lookup form with Playwright.
- Supports cattle, sheep/goats, pigs, and equids.
- Reads identifiers from the command line or a CSV file.
- Exports normalized results to UTF-8 CSV.
- Applies a delay between requests to avoid aggressive traffic.
- Includes a safe smoke test that submits no animal identifier.
- Stores no identifiers, responses, credentials, or analytics in the project.

## Returned fields

The tool extracts only the fields displayed by the public form:

- Animal identifier
- Species query category
- Sex
- Breed
- Date of birth
- Public final-event information, when present

The repository intentionally contains no real animal identifiers, query
results, farm data, credentials, local paths, or personal information.

## Requirements

- Python 3.10 or newer
- Chromium installed through Playwright

## Installation

```bash
git clone https://github.com/MarcoRMz95/italian-livestock-registry-metadata.git
cd italian-livestock-registry-metadata
python -m venv .venv
```

Activate the environment:

```bash
# Windows
.venv\Scripts\activate

# macOS or Linux
source .venv/bin/activate
```

Install the project and Chromium:

```bash
python -m pip install -e .
python -m playwright install chromium
```

## Usage

Query one or more cattle identifiers:

```bash
italian-livestock-metadata <ANIMAL_ID> <ANOTHER_ANIMAL_ID>
```

Choose another public VetInfo category:

```bash
italian-livestock-metadata --species sheep-goats <ANIMAL_ID>
italian-livestock-metadata --species pigs <ANIMAL_ID>
italian-livestock-metadata --species equids <ANIMAL_ID>
```

Read identifiers from a CSV file containing an `animal_code` column:

```bash
italian-livestock-metadata --input input.csv --output results.csv
```

Check that all supported public forms are reachable without submitting an
animal identifier:

```bash
italian-livestock-metadata --smoke-test
```

Show the browser while querying:

```bash
italian-livestock-metadata --headed <ANIMAL_ID>
```

## Tests

The automated tests use only synthetic records:

```bash
python -m unittest discover -s tests -v
```

The CI workflow never queries a real animal.

## Privacy and responsible use

- Input and output CSV files are ignored by Git to reduce accidental uploads.
- The tool does not bypass authentication or access restricted VetInfo areas.
- Use only identifiers you are legally entitled to query.
- Do not use the tool to identify, profile, or contact animal owners.
- Review VetInfo's current policies and applicable law before large-scale use.
- Keep the default delay or increase it. Do not overload the public service.

The public website can change without notice. Parsing rules may therefore need
maintenance over time.

## Data source

Data is retrieved at runtime from the
[public VetInfo animal query](https://www.vetinfo.it/sso_portale/informazioni/int_capi_no_log.pl?P_CAPI=BOV).
VetInfo remains the authoritative source for the returned information.

## License

Released under the [MIT License](LICENSE).

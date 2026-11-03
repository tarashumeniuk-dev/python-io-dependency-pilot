# Publication review

Scope: this repository's code, configuration, recorded results, generated
figures, documentation and dependency notices. The review combined pattern
scans, structured JSON inspection, source/import review, PDF metadata/content
inspection and byte-level comparisons with the private local evidence. No scan
can establish the absence of every possible secret; no credential-like finding
was detected in the reviewed experiment files.

## Removed or excluded

- The local Python executable path in 42 JSON files (40 run records and two
  environment records) contained a personal user-directory name. Only those
  string values were replaced with a redaction marker; numerical measurements,
  configurations and request outcomes were not modified.
- Six PDFs had generated creation/modification dates with a local timezone.
  Document-level metadata was replaced with a neutral producer field; page
  content streams are byte-identical and remain vector.
- One compiled Python cache, 40 empty stderr logs, private QA files, Library
  metadata, the monograph DOCX and obsolete local packaging/checksum records
  are excluded. The old machine-specific README command was replaced with
  portable repository instructions. No `.git` history or credentials is bundled.

## Retained disclosures

The repository exposes synthetic request IDs and outcomes, all request timing
measurements, run order, configuration and random seed, UTC run dates/times,
Windows build, Python/compiler and analysis-library versions, CPU model and
logical CPU count, physical/available memory snapshots, CPU-time diagnostics,
event-loop policy and clock implementation/resolution. These characterize the
shared test computer and its load; they are not production or human-subject data.
The experiment uses loopback address `127.0.0.1`, not a private network endpoint.
The repository account and GitHub-generated commit identity will also be public.

No local computer name, local username, personal executable path, email credential,
API key, private key or access token was found in the prepared experiment content.
Third-party attribution notices retain their licensors' published names/emails.
Only the dependency notices are third-party text; no papers or thesis content
are bundled. No permissive or other project license has been added.

## Preservation and future runs

All 40 raw CSVs, ten derived CSVs, configurations, benchmark source snapshots and
analysis code are unchanged. `PROVENANCE.json` lists unchanged files and exact
JSON field locations changed. `SHA256SUMS.txt` covers the publication copy.
The original private files remain intact separately.

The preserved benchmark will record a local executable path on future runs.
New outputs belong under ignored `runs/`; review their metadata and logs before
publishing them. This review applies to the delivered snapshot, not future data.

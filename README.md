# Blocking and asynchronous Python services under dependency degradation

A reproducible local pilot for a monograph chapter. A generator calls a service,
which calls a synthetic dependency over TCP on `127.0.0.1`. The service uses
either eight blocking worker threads or bounded asyncio dependency calls under
the same asyncio ingress. This is an I/O-handler comparison, not a validation
of Reactive Streams or of a complete reactive architecture.

The implementation was created separately in Python because the earlier
master's-work source code was unavailable. Ferreira's *Reactive Microservices -
An Experiment* (2022) is a research antecedent, not code reproduced here:
https://hdl.handle.net/10400.22/20831 . No thesis text or third-party research
code is bundled.

## Recorded series

| Directory | Role | Clock | Runs | Measured requests |
| --- | --- | --- | --- | --- |
| `results_controlled_clock/` | Main controlled local pilot | `perf_counter` in all three event loops, including private `_clock_resolution` | 20 | 9,600 |
| `results/` | Earlier diagnostic series only | Default event-loop clock | 20 | 9,600 |

Do not pool the series. The initial series prompted investigation of Windows
timer resolution; it is not additional evidence for the chapter's main effect
estimates. The diagnostic series preceded the controlled series, so temporal
changes in the shared computer also confound a between-series comparison.
Each series has five paired repeats at each of 20 and 60 offered requests/s.
The service and dependency restart for each run; the generator process and its
event loop persist throughout a series. These are repeated paired observations
under shared conditions, not fully isolated independent replications.

Main descriptive result: at 60 offered requests/s in the slow cohort, medians
of five run-level p95 client latencies were 1,854.0 ms (blocking) and 1,852.1 ms
(asyncio). Their descriptive min-max ranges overlap. This is neither an
equivalence test nor evidence of universal superiority. The controlled series
contains 6,963 successful responses, 774 admission rejections, 930 explicit
dependency failures and 933 dependency timeouts. Summaries include all statuses;
consult success percentages and successful-request summaries alongside latency.

## Reproduce and inspect

Tested benchmark runtime: CPython 3.12.14 on Windows 11, 12 logical CPUs,
Intel Core i5-12450H, approximately 16.9 GB physical RAM. The benchmark itself
uses only the standard library. Other Python implementations, versions and OSes
require validation, especially the private event-loop attribute.

Use an isolated Python environment with CPython 3.12. The commands below run
from the repository root; `python` must refer to that environment.

```text
python -m pip install -r requirements-reproduction.txt
python verify_package.py
```

These commands inspect the delivered evidence without generating load. All
publication bytes are covered by `SHA256SUMS.txt` (except the checksum file
itself). `verify_package.py` also validates source snapshots, CSV business
results, request counts, paired schedules and configured admission bounds.

To recompute summaries, first copy a series to a new working directory; analysis
writes derived files. For example:

```text
python -c "import shutil; shutil.copytree('results_controlled_clock', 'runs/reanalysis')"
python analyze.py --input runs/reanalysis
```

To conduct a new controlled run (roughly five minutes on the original host):

```text
python benchmark.py --config config.json --output runs/controlled_new
python analyze.py --input runs/controlled_new
```

To reproduce the earlier diagnostic configuration using its exact source snapshot:

```text
python results/benchmark_source.py --config results/config.json --output runs/diagnostic_new
python analyze.py --input runs/diagnostic_new
```

The output directory must not already exist. The two benchmark commands each
start a complete series; they are not required to inspect the existing data.
Use a quiet machine and retain new runs separately. Loopback sockets must be
available; do not disable security controls to make a run succeed. Failure of
socket or event-loop initialization is an environment blocker, not a result.
New runs may differ because host scheduling and background activity vary.

The preserved benchmark records its executable path in new metadata. Keep new
outputs under ignored `runs/` and review/redact them before any later upload;
do not publish a fresh output directory without this check.

## Design and measurement boundaries

- Four sequential three-second request cohorts: normal (20 ms nominal wait),
  slow (200 ms), fault (even IDs: status 503 after 20 ms; odd IDs: 600 ms intended
  to exceed the dependency deadline), and recovered (20 ms successful behaviour).
  The cohort travels with each request. Recovered is a new cohort, not a global
  restoration of dependency state; older fault requests retain their behaviour.
- Eight active calls in each handler, eight blocking pool workers, 64 additional
  waiting requests, 128 unfinished generator-task cap. Each RPC opens a new
  connection; there is no connection pool. Timed-out dependency work can remain
  alive, so eight active service calls is not a global connection bound.
- Nominal dependency budget: 300 ms. The blocking deadline begins inside
  `blocking_rpc`, after thread dispatch. The asynchronous budget begins inside
  `rpc` after admission. Neither covers waiting for admission. Cleanup and some
  dispatch work lie outside those budgets. Generator I/O budget: five seconds.
- Open-loop planned arrival schedule; generator launch lag is separately
  recorded. `latency_ms` is actual send-to-completion time and
  `scheduled_latency_ms` is planned-arrival-to-completion time. `queue_ms` and
  `dependency_ms` have service-side boundaries. Complete-call timing does not
  measure the dependency's actual sleep duration.
- Synthetic successful business result: `value = id * 2 + 1`; unsuccessful
  responses have null values. Five semantic probes and 32 warm-up requests per
  run are excluded from the timed workload. No retries, circuit breaker, HTTP,
  TLS, persistence or real database are implemented.
- `generator_cpu_s` includes final control RPCs, sorting, checks, field
  preparation and CSV writing; it is not isolated load-generation CPU time.
  CPU sampling boundaries differ across components. Available-memory snapshots
  are host diagnostics, not process RSS or allocation measurements.
- All three processes share one computer with other applications. CPU affinity,
  power management and background load were uncontrolled. Three-second cohorts,
  five pairs and a single concurrency level limit inference. No superiority,
  equivalence or non-inferiority test was performed. Simulated delay does not
  reproduce a real database or network failure.

## Files and data dictionary

`benchmark.py` and each `benchmark_source.py` are preserved byte-for-byte;
`analyze.py` is the audited analysis implementation. `config.json` configures the
controlled run. Each series retains its own configuration, manifest, clock
properties, environment metadata, semantic probes, completion and validation
records. Historical manifest commands are provenance, not portable commands;
use the commands above after checkout.

Each `*rps_rep*.csv` is a measured run. There are 40 raw CSV files across both
series, 19,200 measured requests in total; there are another ten derived CSVs.
All 50 CSV files are unchanged from the local experiment.

| Fields | Meaning |
| --- | --- |
| `run`, `rate`, `repeat`, `mode`, `phase` | Run and planned workload identifiers |
| `id`, `value`, `semantic_ok` | Synthetic request ID, business result, contract check |
| `status` | 200 success; 429 admission rejection; 503 explicit failure; 504 dependency timeout; 598/599 generator timeout/error (absent in recorded series) |
| `planned_s`, `sent_s`, `ended_s` | Relative times from run origin, seconds |
| `launch_lag_ms` | Actual send minus planned arrival |
| `latency_ms`, `scheduled_latency_ms` | Client completion intervals described above |
| `queue_ms`, `dependency_ms` | Service-side intervals; may be absent for rejected calls |

`summary_by_run_phase.csv` retains per-run/cohort summaries, including successful
responses; `aggregate.csv` has medians and descriptive ranges over runs;
`paired_differences.csv` stores within-pair comparisons; `diagnostics.csv` and
`timer_diagnostics.csv` record measurement diagnostics. `report.html` and the
three SVG/PDF figures per series are generated outputs. All supplied figures
are vector; no bitmap previews are included. The original Ukrainian protocol
and summary are retained in `PROTOCOL.txt` and `RESULTS_SUMMARY_UK.txt`.

## Publication provenance and permissions

See `PRIVACY_REVIEW.md` and `PROVENANCE.json` for the narrowly scoped publication
redactions. The original local evidence was not altered. No Git credentials,
local usernames, computer names, absolute personal paths, monograph document,
Library identities, QA screenshots, environment folders or compiled caches are
included. Hardware/software characteristics and UTC experiment times remain
disclosed as reproducibility metadata.

No license granting third-party rights has been selected for the original code,
data or documentation. See `LICENSING.md`. Dependency notices are separately
preserved in `third_party_licenses/`; their licenses do not license this project.
The dependency versions in `requirements-reproduction.txt` were checked during
publication preparation, while historical recorded analysis versions are in
each series' `analysis_environment.json`.

# Daily Proverb Overlay

Fetches the Wikimedia Commons Picture of the Day, composites a quote onto it, and
writes the image alongside the attribution its license requires.

Currently a local script: there is no pipeline or deployment yet.

> **The overlay text is placeholder Lorem Ipsum.** The quote source is not chosen
> yet, so `LoremIpsumProvider` fills in. Output is marked
> `"is_placeholder": true` in `metadata.json` and the CLI says so on every run.

## Install

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Run

Wikimedia's User-Agent policy requires a descriptive agent with a way to reach
the operator; requests without one get blocked. Set it once per session:

```powershell
$env:POTD_CONTACT = "https://github.com/<you>/daily-proverb-overlay"
```

Then:

```powershell
daily-proverb-overlay                      # today's POTD (UTC)
daily-proverb-overlay --date 2026-09-28    # a specific day
daily-proverb-overlay --dry-run -v         # fetch metadata, print attribution, write nothing
daily-proverb-overlay --force              # rebuild a day that is already complete
```

Output lands in `output/YYYY-MM-DD/`:

| File | What it is |
| --- | --- |
| `source.jpg` | The downloaded thumbnail. A cache — safe to delete. |
| `overlay.jpg` | The composited image. This is the thing you publish. |
| `attribution.txt` | Full credit text. Publish this *with* the image. |
| `metadata.json` | Machine-readable record of the run, for the archive later. |

## Structure

```
src/daily_proverb_overlay/
├── cli.py            argument parsing, logging, exit codes
├── pipeline.py       the daily job — the only module that knows the order
├── config.py         settings from CLI flags, then environment, then defaults
├── models.py         plain dataclasses passed between stages
├── http_client.py    retry-safe GETs with a policy-compliant User-Agent
├── attribution.py    Commons metadata → credit line and full attribution text
├── storage.py        output paths, atomic writes, the idempotency check
├── sources/
│   ├── wikimedia.py  Commons API → PictureOfTheDay
│   └── quotes.py     QuoteProvider protocol + the Lorem Ipsum placeholder
└── render/
    ├── fonts.py      finding a real TrueType file (breaks first in containers)
    ├── layout.py     font-metric text wrapping and size fitting
    └── compositor.py scrim, quote, credit → final image
```

Dependencies are injected rather than constructed inline, so stages can be tested
against fakes with no network access.

### Why it is safe to rerun

Three things, all in `storage.py` and `http_client.py`:

- Output for a date lives in one directory named after that date.
- `is_complete()` requires *every* published file, so a partial run counts as
  unfinished rather than done.
- Writes go to a temp file beside the target and are then `os.replace()`d in, so
  a crash mid-write can never leave a truncated file that looks complete.

The final image is written **last** for the same reason: a crash after the
attribution but before the image leaves the day correctly marked incomplete.

The placeholder quote provider is seeded by date, not random, so a rerun produces
byte-identical text — which is what makes the idempotency claim testable.

## Exit codes

CI needs to tell "nothing published yet" apart from "the job is broken":

| Code | Meaning |
| --- | --- |
| 0 | Success, or a day already complete |
| 1 | Unexpected failure |
| 2 | Bad invocation |
| 3 | No POTD published for that date |
| 4 | Configuration problem (missing contact, no font found) |

## Licensing

Commons only accepts free licenses, so overlaying is always permitted. Most POTDs
are **CC BY-SA**, which makes the overlay a derivative that inherits ShareAlike —
`attribution.txt` states this whenever the source license indicates SA.

Attribution is read from the `extmetadata` fields (`ObjectName`, `Artist`,
`Credit`, `LicenseShortName`, `LicenseUrl`) and never hardcoded. Fields are
genuinely often absent; missing ones degrade to `Unknown author` rather than
guessing.

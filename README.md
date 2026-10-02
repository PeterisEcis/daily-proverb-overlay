# Daily Proverb Overlay

Fetches the Wikimedia Commons Picture of the Day, picks an English proverb from
Wiktionary, runs it through a chain of machine translations until it comes back
garbled, and composites the result onto the image — with the original proverb
underneath, and the attribution the image's license requires alongside.

The default chain is English → Japanese → Swahili → Georgian → Finnish → Chinese →
Basque → Malagasy → Thai → English: eight unrelated language families, each
dropping something English needs to get back (articles, plurals, gendered
pronouns, tense, word order).

Currently a local script: there is no pipeline or deployment yet.

## Install

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

## Run

Settings live in a `.env` file in the directory you run from. Start one from the
template, which lists every variable with its default:

```powershell
Copy-Item .env.example .env
```

`.env` is gitignored. Real environment variables override it and CLI flags
override both, so CI can provide the same names as secrets with no file at all.

Two values need filling in:

- **`POTD_CONTACT`** — a repo URL or email. Wikimedia's User-Agent policy requires
  a descriptive agent with a way to reach the operator, and blocks requests
  without one.
- **`POTD_GOOGLE_TRANSLATE_API_KEY`** — for the translation chain. Without it the
  run still works, but the proverb goes on the image untranslated and a warning
  says so. The key has no CLI flag, so it never lands in shell history. To get one:

  1. In the [Google Cloud console](https://console.cloud.google.com/), create a
     project and attach a billing account. A daily run translates roughly 400
     characters, far inside the free monthly allowance — but check current pricing.
     The default quota is unlimited, which means *uncapped*, not *free*: consider
     setting a daily character cap so a bug or leaked key cannot run up a bill.
  2. Enable the **Cloud Translation API** for the project.
  3. Under *APIs & Services → Credentials*, create an API key and restrict it to
     the Cloud Translation API.

Then:

```powershell
daily-proverb-overlay                      # today's POTD (UTC)
daily-proverb-overlay --date 2026-09-28    # a specific day
daily-proverb-overlay --dry-run -v         # fetch and translate, print the result, write nothing
daily-proverb-overlay --force              # rebuild a day that is already complete
daily-proverb-overlay --languages ko,yo,hu # a different translation chain
daily-proverb-overlay --languages ""       # no translation
daily-proverb-overlay --quote-provider lorem  # placeholder text, no Wiktionary call
```

`--dry-run` is the cheap way to try out a chain: it prints the translated proverb
without rendering anything.

Output lands in `output/YYYY-MM-DD/`:

| File | What it is |
| --- | --- |
| `source.jpg` | The downloaded thumbnail. A cache — safe to delete. |
| `overlay.jpg` | The composited image. This is the thing you publish. |
| `attribution.txt` | Full credit text. Publish this *with* the image. |
| `metadata.json` | Machine-readable record of the run, for the archive later. |

## Deployment

[`.github/workflows/daily.yml`](.github/workflows/daily.yml) runs the job every day
at 00:17 UTC on GitHub's machines, commits the new `output/YYYY-MM-DD/` folder back
to `main`, rebuilds the site and publishes it to GitHub Pages:
<https://peterisecis.github.io/daily-proverb-overlay/>

It needs, under the repository's *Settings*:

| Where | What |
| --- | --- |
| Pages → Build and deployment → Source | **GitHub Actions** |
| Secrets and variables → Actions → Secrets | `POTD_GOOGLE_TRANSLATE_API_KEY` |
| Secrets and variables → Actions → Variables | `POTD_CONTACT` (the repo URL keeps your email out of public logs) |

To run it by hand, open the *Actions* tab → *Daily overlay* → *Run workflow*. It
takes an optional date and a *force* switch, same as the CLI flags.

The site is plain static HTML, built from `output/` by a second command:

```powershell
daily-proverb-site              # output/ → _site/, rebuilt from scratch
```

Open `_site/index.html` in a browser to preview it locally.

The archive lives in the repo, so `output/` is committed — except `source.*`, the
downloaded original, which is only a cache. Each day adds about 1 MB.

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
├── site_builder.py   output/ → the static archive site (daily-proverb-site)
├── sources/
│   ├── wikimedia.py  Commons API → PictureOfTheDay
│   ├── wiktionary.py Category:English proverbs → one proverb per day
│   ├── translation.py  the translation chain + Google Cloud Translation client
│   └── quotes.py     QuoteProvider protocol, registry, Lorem Ipsum placeholder
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

Proverb selection is deterministic by date, not random, so a rerun picks the same
proverb. The translation is not guaranteed to match on a `--force` rebuild:
Google updates its models, and the same input can come back differently months
later. `metadata.json` records every hop of the chain, so the published version
is never lost.

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

Only the proverb itself — the Wiktionary page title — is used, never the
definition text, which is CC BY-SA. `metadata.json` links the Wiktionary entry.

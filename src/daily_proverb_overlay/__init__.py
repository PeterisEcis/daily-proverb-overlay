"""Daily Proverb Overlay.

Fetches the Wikimedia Commons Picture of the Day, composites a quote onto it,
and emits the image together with the attribution the license requires.

Layout of the package:

    config.py        settings, resolved from CLI flags and environment
    models.py        plain dataclasses passed between stages
    http_client.py   retry-safe HTTP with a policy-compliant User-Agent
    attribution.py   turns Commons metadata into credit text
    sources/         things that fetch (Commons, quotes)
    render/          things that draw (fonts, text layout, compositing)
    storage.py       where output lands, and the idempotency check
    pipeline.py      the daily job, wiring the above together
    cli.py           argument parsing and logging
"""

__all__ = ["__version__"]

__version__ = "0.1.0"

# Bundled fonts

Drop a `.ttf` or `.otf` here and `FontResolver` finds it before any system font.
If several are present, the alphabetically first one wins.

This directory is empty by design right now: on a developer machine the resolver
falls back to system fonts, so there is nothing to bundle yet.

It stops being empty once this is **containerised**. A `python:*-slim` image ships
no fonts at all, so the resolver that works locally finds nothing in the image and
raises `FontNotFoundError`. Two ways out:

1. Install fonts in the image:
   ```dockerfile
   RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*
   ```
2. Commit a font here and the image renders identically to your laptop,
   regardless of the base image.

Option 2 is more reproducible, but only legal for fonts licensed to be
redistributed. Safe choices, all SIL Open Font License:

- [DejaVu Sans](https://dejavu-fonts.github.io/)
- [Inter](https://rsms.me/inter/)
- [Noto Sans](https://fonts.google.com/noto) — widest script coverage, which
  matters if the quote language ever follows the image's country of origin

Avoid Arial, Calibri, Segoe UI and the other Microsoft or Apple system fonts:
they are fine to *use* from an installed OS, but not to redistribute in an image.

# Self-hosted web fonts

`fonts.css` plus the `.woff2` files beside it replace the four
`fonts.googleapis.com/css2` stylesheets the site used to link. Nothing here
reaches a third-party CDN: no `fonts.googleapis.com` stylesheet request, no
`fonts.gstatic.com` font request.

Families covered — Fira Code, Noto Sans, Inter, Roboto, Roboto Mono.

## How it works

`fonts.css` carries Google's own `@font-face` blocks verbatim, with each `src`
repointed at a local file. `unicode-range` is preserved, so a browser still
downloads only the subsets a page actually needs — a Vietnamese page fetches
the `vietnamese` and `latin` cuts and ignores the Cyrillic, Greek, Devanagari
and symbol ones. All 43 files together are ~1 MB; a typical page pulls a small
fraction of that.

Only `woff2` is shipped. Every browser that has supported this site for years
supports it, and carrying `woff`/`ttf` fallbacks would roughly triple the size
for no one.

## Regenerating

Edit `SOURCES` in `generate.py` if the set of families changes, then:

```bash
cd resources/libs/fonts && python3 generate.py
```

It refetches every stylesheet and font file and rewrites `fonts.css`. Stale
files are not pruned — check `git status` afterwards and delete what is no
longer referenced.

`fonts.css` is generated. Do not hand-edit it; change `generate.py` instead.

## After changing anything here

`resources/` is behind Cloudflare's 4-hour `/static/` cache, so bump the `?v=`
token as usual — see the cache-busting rule in the operations playbook. The
`<link>` lives in `templates/base.html`, and the same file is listed in the
admin pagedown widget's `Media` in `judge/widgets/pagedown.py`.

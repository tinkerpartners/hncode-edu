# Self-hosted KaTeX 0.16.9

Vendored from the `katex@0.16.9` npm tarball, replacing the
`cdn.jsdelivr.net/npm/katex@0.16.9` links the site used to carry. Nothing here
reaches a third-party CDN.

```
katex.min.css              dist/katex.min.css
katex.min.js               dist/katex.min.js
contrib/auto-render.min.js dist/contrib/auto-render.min.js
fonts/                     dist/fonts/  (all 20 faces, woff2 + woff + ttf)
LICENSE                    MIT
```

`katex.min.css` references `fonts/KaTeX_*.woff2` relatively, which is why
`fonts/` has to stay a sibling of the CSS. All three font formats are kept, as
upstream ships them, so the `src` fallback chain in the CSS resolves for every
browser rather than 404ing against our own origin.

The three files carry the same bytes the CDN was serving: their SHA-384 digests
match the `integrity` attributes that were pinned in
`templates/katex-load.html` before the switch.

```
katex.min.css               sha384-n8MVd4RsNIU0tAv4ct0nTaAbDJwPJzDEaqSD1odI+WdtXRGWt2kTvGFasHpSy3SV
katex.min.js                sha384-XjKyOOlGwcjNTAIQHIpgOno0Hl1YQqzUOEleOLALmuqehneUG+vnGctmUb0ZY0l8
contrib/auto-render.min.js  sha384-+VBxd3r6XgURycqtZ117nYw44OOcIax56Z4dCRWbxyPt0Koah1uHoK0o4+/RRE05
```

Verify with:

```bash
openssl dgst -sha384 -binary katex.min.js | openssl base64 -A
```

## Upgrading

```bash
curl -sSL -o katex.tgz https://registry.npmjs.org/katex/-/katex-<version>.tgz
tar xzf katex.tgz
cp package/dist/katex.min.{css,js} package/LICENSE .
cp package/dist/contrib/auto-render.min.js contrib/
cp -r package/dist/fonts/. fonts/
```

Then bump the `?v=` token in `templates/katex-load.html` — `/static/` sits
behind Cloudflare's 4-hour cache. `judge/widgets/pagedown.py` lists the same
files in `KatexPagedownWidget.Media` and needs no change unless paths move.

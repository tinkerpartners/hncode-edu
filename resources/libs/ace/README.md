# Self-hosted Ace 1.32.6

The contents of `ace-builds@1.32.6`'s **`src-min`** directory, plus the BSD
LICENSE. This replaces `https://cdnjs.cloudflare.com/ajax/libs/ace/1.32.6/`,
which is what `ACE_URL` pointed at in each box's `local_settings.py`.

`src-min`, not `src-min-noconflict` — cdnjs mirrors `src-min`, and the two
differ. Verified: `ace.js`, `mode-c_cpp.js`, `theme-github.js`,
`theme-monokai.js`, `worker-javascript.js`, `ext-language_tools.js` and
`snippets/c_cpp.js` are byte-identical to what cdnjs served.

```
sha256(ace.js)  6713d34f62ab1c9185d23a49ef414a2c2ed3e4075bca1b03b04b24b128e8fbd9
```

## Why the whole directory

`ace.js` resolves `mode-*.js`, `theme-*.js`, `worker-*.js`, `ext-*.js` and
`snippets/*.js` lazily at runtime, against its own base path. Which mode a page
needs comes from `Language.ace` in the database, so it is not knowable from the
source tree — an admin adding a language later would silently break the editor
if we had shipped only the modes in use at vendoring time.

That matters most in exactly the situation this was done for: a locked-down
contest network where the browser cannot fall back to a CDN. So all 278 files
ship (~9 MB). Pruning to the modes actually configured would save most of that
and is a reasonable trade if repo size becomes a problem — but it turns a
missing mode into a contest-day failure rather than a slow page.

## Upgrading

```bash
curl -sSL -o ace.tgz https://registry.npmjs.org/ace-builds/-/ace-builds-<version>.tgz
tar xzf ace.tgz
rm -rf resources/libs/ace && cp -r package/src-min resources/libs/ace
cp package/LICENSE resources/libs/ace/
```

`ACE_URL` is set in `dmoj/settings.py` as `STATIC_URL + "libs/ace/"` — the
trailing slash is load-bearing, because `FileEditWidget` builds its script URL
with `urljoin(ACE_URL, "ace.js")` and without one `urljoin` drops the last path
segment. Check that no box still overrides `ACE_URL` in `local_settings.py`.

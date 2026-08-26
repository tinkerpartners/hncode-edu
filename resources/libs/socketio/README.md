# Self-hosted Socket.IO client

`socket.io.min.js` is the Socket.IO **4.7.2** browser client, taken verbatim from
`https://cdn.socket.io/4.7.2/socket.io.min.js` — the URL `templates/event-load.html`
used to link.

```
sha256  83df4abc7eec941f1d29ae254e80bac0bb82d398fbe2e8ee4ea2a7efc8e704f1
```

The version has to keep matching the server the event daemon runs
(`websocket/`), so do not bump it here alone.

After replacing the file, bump the `?v=` token in `templates/event-load.html` —
`/static/` sits behind Cloudflare's 4-hour cache.

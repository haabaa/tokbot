# Token Monitor (Telegram + Dashboard + PWA)

Watches  and notifies on changes for the selected room.

## Telegram commands
- `/startwatch Room 09`
- `/stopwatch`
- `/status`

## Web dashboard
- `/` landing page redirects to `/login`
- `/dashboard` (protected)

## Theme
Theme cycles: **System → Dark → Light**
- System auto-syncs with device theme changes.

## Session timeout
Dashboard shows countdown. Configure via `SESSION_TIMEOUT_MIN`.

## Environment variables (Coolify)
Required:
- `BOT_TOKEN`
- `CHAT_ID`
- `DASH_USER`
- `DASH_PASS`
- `SECRET_KEY`

Optional:
- `POLL_SECONDS` (default 15)
- `STATE_PATH` (default `/app/data/caretrust_state.json`)
- `SESSION_TIMEOUT_MIN` (default 30)

## Coolify notes
- Expose port `8080`.
- Add persistent storage mount to `/app/data` so state survives redeploys.
- Behind Traefik HTTPS is expected because `SESSION_COOKIE_SECURE=True`.

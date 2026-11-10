# Fantasy What-If Frontend

The Fantasy What-If frontend is a React and TypeScript single-page application built with Vite.

## Production Architecture

Production uses a multi-stage container build:

1. Node.js installs locked dependencies.
2. Vite builds the static application.
3. Unprivileged Nginx serves the static assets.
4. Nginx proxies `/api/` and `/health` to the backend.
5. Browser routes fall back to `index.html`.

The production image runs as the unprivileged `nginx` user on port `8080`.

## Development

Install dependencies:

```bash
npm ci
```

Start Vite:

```bash
npm run dev
```

The development configuration supports the code-server proxy path:

```text
/proxy/5173/
```

Production builds use the root path:

```text
/
```

## Checks

Run lint:

```bash
npm run lint
```

Run tests:

```bash
npm test
```

Build production assets:

```bash
npm run build
```

Audit dependencies:

```bash
npm audit --audit-level=low
```

## Production Image

Build locally:

```bash
docker build \
  --file Dockerfile \
  --tag fantasy-what-if-frontend:local \
  .
```

Inspect the runtime user:

```bash
docker image inspect \
  fantasy-what-if-frontend:local \
  --format 'User={{.Config.User}}'
```

Expected result:

```text
User=nginx
```

## Routes

The frontend supports:

- `/`
- `/setup`
- `/login`
- `/admin`
- `/all-play`
- `/league-average`
- `/schedule-swap`
- `/optimal-lineups`
- `/optimal-lineups/:teamId`

Nginx returns `index.html` for browser routes.

## API Access

The frontend sends same-origin requests through:

```text
/api
```

Authentication uses cookies and CSRF tokens.

State-changing requests send:

```text
X-CSRF-Token
```

The frontend does not embed the backend hostname into the production JavaScript bundle.

## Static Asset Caching

Hashed assets under `/assets/` use long-lived immutable caching.

`index.html` uses cache-prevention headers so new deployments are detected promptly.

## Security Headers

The production frontend adds:

- Content Security Policy
- MIME-sniffing prevention
- Frame embedding denial
- Referrer restrictions
- Permissions Policy
- Cache prevention for `index.html`

See the root README and docs/DEVELOPMENT.md for the complete project architecture.
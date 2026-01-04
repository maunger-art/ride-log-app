# Deployment guide

## Choose a provider

Pick a provider that can host a FastAPI service and serve a static React build. Examples include:

- **Render** (simple web service + static build)
- **Railway** / **Fly.io** (more control)
- **Vercel + separate API host** (split hosting)

## Build the React app

From the repo root:

```bash
cd frontend
npm install
npm run build
```

This produces `frontend/dist`, which is served at `/` by the FastAPI app.

## Run the FastAPI app

Use the combined app entrypoint:

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

The API is available under `/api` and the React app is served from `/`.

## Configure environment variables

Set these environment variables in the platform dashboard:

- `SUPABASE_URL` (use the exact project URL, formatted like `https://<project-ref>.supabase.co`)
- `SUPABASE_KEY` (use the anon/public key)
- `SUPABASE_EMAIL_REDIRECT` (optional, where magic-link emails should return users; e.g. `https://your-app.example.com`)
- (Optional) Strava variables if you use that feature.

## Verify Supabase auth settings

1. In Supabase, ensure **email/password auth** and **email OTP/magic links** are enabled for the project.
2. Add the env variables above to your hosting provider’s settings.
3. Restart/redeploy the app so the new configuration is picked up.

## Add a custom domain

1. After deployment, copy the provider’s default URL (for example, `your-app.onrender.com`).
2. In the host’s dashboard, add your custom domain (for example, `app.techniquehealth.org`).
3. Complete any SSL/TLS validation steps (most platforms auto-issue TLS once DNS is correct).

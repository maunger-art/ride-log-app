# Deployment guide

## Choose a provider

Pick the hosting provider that fits your needs:

- **Streamlit Community Cloud** (fastest, simplest)
- **Render** (more control, paid tiers available)
- **Railway** / **Fly.io** (advanced options)

## Deploy from GitHub

1. Connect your GitHub account to the provider.
2. Deploy the repository from the `main` branch.

## Configure environment variables

Set these environment variables in the platform dashboard:

- `SUPABASE_URL` (use the exact project URL, formatted like `https://<project-ref>.supabase.co`)
- `SUPABASE_KEY` (use the anon/public key)
- (Optional) Strava variables if you use that feature.

## Verify Supabase auth settings

1. In Supabase, ensure **email/password auth** is enabled for the project.
2. Add the env variables above to your hosting provider’s settings.
3. Restart/redeploy the app so the new configuration is picked up.

## Add a custom domain

1. After deployment, copy the provider’s default URL (for example, `your-app.streamlit.app`).
2. In the host’s dashboard, add your custom domain (for example, `app.techniquehealth.org`).
3. Complete any SSL/TLS validation steps (most platforms auto-issue TLS once DNS is correct).

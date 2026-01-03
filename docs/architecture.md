# Ride Log App – Architecture Overview

This application supports both coach-facing and client-facing workflows
for endurance training, performance monitoring, and strength & conditioning.

## Core Principles
- Client-facing first (patients can log rides and S&C actuals)
- Coach oversight with editable plans
- Neutral training templates with linear progression
- Safe, auditable strength estimation (no direct 1RM input)

## Tech Stack
- Streamlit (UI)
- SQLite (local persistence)
- Supabase Auth (authentication)
- Strava API (client-owned connections)


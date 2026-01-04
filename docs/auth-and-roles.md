# Authentication & Roles

## Authentication
- Supabase Auth
- Coaches authenticate via email/password
- Clients authenticate via email login link (magic link)
- Supabase user ID is mapped to internal roles

## Roles
### Client
- Accounts are created by coaches, who trigger an email login link
- Can log rides
- Can connect own Strava account
- Can view plans and S&C targets
- Can enter S&C actuals
- Cannot edit plans or templates

### Coach
- Can view all assigned patients
- Can edit plans and S&C blocks
- Can review actuals vs targets
- Cannot see patients not explicitly assigned

### Super Admin (Account Owner)
- Owns the organisation account
- Can view and manage all patients in the organisation
- Can add/remove coaches in the organisation
- Has coach-level edit permissions

## Access Control
- Enforced both in UI and database layer
- All patient access validated via role mapping

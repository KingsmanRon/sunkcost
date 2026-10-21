# sunkcost-api

The API service (ARCHITECTURE.md §4 C6): FastAPI on Uvicorn, psycopg 3 against the Supabase
transaction pooler with prepared statements disabled. Configuration is read from the environment
(§11.3); see `sunkcost_api/config.py`. Run locally with `supabase start && docker compose up`.

-- Local development only (supabase db reset). Never pushed to staging or production, where the
-- owner sets real role passwords. These match the local DATABASE_URLs in docker-compose.yml.
alter role sunkcost_api with password 'sunkcost_api_local';
alter role sunkcost_worker with password 'sunkcost_worker_local';

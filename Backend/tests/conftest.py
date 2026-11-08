import os

# Dummy settings so the app can import without a real .env
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_ANON_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_JWT_SECRET", "test-secret-test-secret-test-secret")
# Empty Cognito ids beat a real .env file, so tests never fetch the real pool's keys.
# (Values already exported in the shell still win: run the suite from a clean shell.)
os.environ.setdefault("COGNITO_USER_POOL_ID", "")
os.environ.setdefault("COGNITO_STAFF_CLIENT_ID", "")
os.environ.setdefault("COGNITO_AGENT_CLIENT_ID", "")

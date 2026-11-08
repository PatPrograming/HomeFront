"""Create the HomeFront DynamoDB tables if missing (DynamoDB Local / dev).

In AWS, tables come from infra/cloudformation/homefront.yaml instead.
Reads DYNAMODB_ENDPOINT_URL, DYNAMODB_TABLE_PREFIX and AWS_REGION like the app.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Backend"))

from app.core.dynamodb import create_tables, table_name, TABLES  # noqa: E402

for attempt in range(20):
    try:
        created = create_tables()
        break
    except Exception as exc:  # endpoint still starting
        if attempt == 19:
            raise
        time.sleep(0.5)

for name in TABLES:
    full = table_name(name)
    print(f"{'created' if full in created else 'exists '}  {full}")

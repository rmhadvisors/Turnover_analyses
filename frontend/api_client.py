"""Single place for all calls from the frontend to the backend REST API."""
import httpx

BASE_URL = "http://localhost:8000"


def health_check() -> dict:
    response = httpx.get(f"{BASE_URL}/health")
    response.raise_for_status()
    return response.json()

# Client, import, entry, threshold, report, and alert API calls are added
# here in later stages as the corresponding backend routers are built.

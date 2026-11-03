"""The stock check, run by the Guidal cron job `stock-check` every 2 minutes.

A cron job cannot be given the database, so it asks the web service to run the check
over the project's private network. Needs INTERNAL_TOKEN (project secret, never logged).
Exit code 0 = the check ran.
"""

import os
import sys
import urllib.request

URL = os.environ.get("STOCK_CHECK_URL", "http://web:3000/internal/stock-check")


def main() -> int:
    token = os.environ.get("INTERNAL_TOKEN")
    if not token:
        print("INTERNAL_TOKEN not set: give the project secret to this cron job", flush=True)
        return 2
    request = urllib.request.Request(URL, method="POST", headers={"X-Internal-Token": token})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            print(response.read().decode("utf-8", "replace"), flush=True)
            return 0
    except Exception as exc:  # the status or error class only; never the request headers
        print(f"stock check failed: {getattr(exc, 'code', type(exc).__name__)}", flush=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())

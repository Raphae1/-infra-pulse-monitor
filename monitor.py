import asyncio
import json
import logging
import sqlite3
import time
from typing import Dict, Any
import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)

CONFIG_PATH = "targets.json"

def init_db(db_path: str):
    """Initializes the SQLite database and logs table."""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            target_name TEXT NOT NULL,
            target_url TEXT NOT NULL,
            status_code INTEGER,
            latency_ms REAL,
            status TEXT NOT NULL,
            error_message TEXT
        )
    """)
    conn.commit()
    conn.close()

def log_result(db_path: str, name: str, url: str, status_code: int, latency_ms: float, status: str, error_msg: str = None):
    """Persists a health check entry into SQLite."""
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO audit_logs (target_name, target_url, status_code, latency_ms, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (name, url, status_code, latency_ms, status, error_msg))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Failed to write log to database: {e}")

async def check_target(client: httpx.AsyncClient, target: Dict[str, Any], global_settings: Dict[str, Any]):
    """Periodically performs health checks on a specific target."""
    name = target["name"]
    url = target["url"]
    interval = target.get("interval", global_settings.get("default_interval_seconds", 30))
    timeout = global_settings.get("timeout_seconds", 5)
    db_path = global_settings.get("db_path", "infra_pulse.db")

    while True:
        start_time = time.perf_counter()
        status_code = None
        latency_ms = 0.0
        status = "DOWN"
        error_message = None

        try:
            response = await client.get(url, timeout=timeout)
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            status_code = response.status_code
            
            if response.is_success:
                status = "UP"
            else:
                status = "DEGRADED"
                error_message = f"HTTP {status_code}"

        except httpx.TimeoutException:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            error_message = "Request Timeout"
        except httpx.RequestError as exc:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            error_message = f"Connection Error: {exc}"

        log_result(db_path, name, url, status_code, latency_ms, status, error_message)
        logging.info(f"[{status}] {name} ({url}) - {latency_ms}ms | Code: {status_code} | Error: {error_message}")

        await asyncio.sleep(interval)

async def main():
    with open(CONFIG_PATH, "r") as f:
        config = json.load(f)

    settings = config.get("settings", {})
    targets = config.get("targets", [])
    db_path = settings.get("db_path", "infra_pulse.db")

    init_db(db_path)
    logging.info(f"Initialized database at {db_path}. Monitoring {len(targets)} targets...")

    async with httpx.AsyncClient() as client:
        tasks = [check_target(client, target, settings) for target in targets]
        await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("InfraPulse Monitor stopped by user.")
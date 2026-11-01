import asyncio
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
import httpx

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("InfraPulse")

DB_FILE = "infra_pulse.db"
CONFIG_FILE = "targets.json"

# Track previous states in memory to detect transitions (e.g., UP -> DOWN)
previous_states = {}


def init_db():
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            target_name TEXT NOT NULL,
            url TEXT NOT NULL,
            status_code INTEGER,
            latency_ms REAL,
            status TEXT NOT NULL,
            error_message TEXT
        )
    """)
    
    # Auto-migration check: add 'url' column if running against an older database version
    cursor.execute("PRAGMA table_info(audit_logs)")
    columns = [col[1] for col in cursor.fetchall()]
    if "url" not in columns:
        logger.info("Migrating database: adding missing 'url' column to audit_logs...")
        cursor.execute("ALTER TABLE audit_logs ADD COLUMN url TEXT NOT NULL DEFAULT ''")
        
    conn.commit()
    conn.close()


def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Failed to load configuration from {CONFIG_FILE}: {e}")
        sys.exit(1)


async def send_webhook(webhook_url: str, target_name: str, status: str, details: str):
    if not webhook_url:
        return
    
    # Format payload for Discord / Slack compatible webhooks
    color = 65280 if status == "UP" else 16711680  # Green for UP, Red for DOWN/DEGRADED
    payload = {
        "content": f"🚨 **InfraPulse Alert**: `{target_name}` is now **{status}**!",
        "embeds": [
            {
                "title": f"Target Status: {status}",
                "description": details,
                "color": color,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
        ]
    }

    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(webhook_url, json=payload, timeout=5.0)
            if response.status_code >= 400:
                logger.warning(f"Failed to deliver webhook: HTTP {response.status_code}")
        except Exception as e:
            logger.error(f"Error sending webhook notification: {e}")


async def check_target(client: httpx.AsyncClient, target: dict, webhook_url: str):
    name = target["name"]
    url = target["url"]
    expected_status = target.get("expected_status", 200)
    timeout = target.get("timeout", 5.0)

    start_time = asyncio.get_event_loop().time()
    status = "UP"
    status_code = None
    error_msg = None

    try:
        response = await client.get(url, timeout=timeout, follow_redirects=True)
        latency = (asyncio.get_event_loop().time() - start_time) * 1000
        status_code = response.status_code

        if status_code != expected_status:
            status = "DEGRADED"
            error_msg = f"Expected status {expected_status}, got {status_code}"
    except httpx.TimeoutException:
        latency = (asyncio.get_event_loop().time() - start_time) * 1000
        status = "DOWN"
        error_msg = "Request timed out"
    except Exception as e:
        latency = (asyncio.get_event_loop().time() - start_time) * 1000
        status = "DOWN"
        error_msg = str(e)

    # Check for state transition and trigger webhook if changed
    global previous_states
    last_state = previous_states.get(name)
    if last_state and last_state != status:
        logger.warning(f"State transition detected for {name}: {last_state} -> {status}")
        detail_text = f"URL: {url}\nLatency: {latency:.2f}ms\nError: {error_msg or 'None'}"
        asyncio.create_task(send_webhook(webhook_url, name, status, detail_text))
    
    previous_states[name] = status

    # Log to SQLite
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(DB_FILE)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO audit_logs (timestamp, target_name, url, status_code, latency_ms, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
            (timestamp, name, url, status_code, latency, status, error_msg),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Database write error: {e}")

    logger.info(f"[{status}] {name} ({url}) - {latency:.2f}ms | Code: {status_code} | Error: {error_msg}")


async def monitor_loop():
    init_db()
    config = load_config()
    targets = config.get("targets", [])
    interval = config.get("check_interval_seconds", 30)
    webhook_url = config.get("webhook_url", "")

    logger.info(f"Initialized database at {DB_FILE}. Monitoring {len(targets)} targets every {interval}s...")

    async with httpx.AsyncClient() as client:
        while True:
            tasks = [check_target(client, target, webhook_url) for target in targets]
            await asyncio.gather(*tasks)
            await asyncio.sleep(interval)


def main():
    try:
        asyncio.run(monitor_loop())
    except KeyboardInterrupt:
        logger.info("InfraPulse Monitor stopped by user.")


if __name__ == "__main__":
    main()
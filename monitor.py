import asyncio
import json
import logging
import os
import re
import sqlite3
import sys
import argparse
import ssl
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse
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

previous_states = {}


def init_db():
    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    
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
    
    cursor.execute("PRAGMA table_info(audit_logs)")
    columns = [col[1] for col in cursor.fetchall()]
    
    if "target_url" in columns and "url" not in columns:
        logger.info("Migrating database: renaming 'target_url' to 'url'...")
        cursor.execute("ALTER TABLE audit_logs RENAME COLUMN target_url TO url")
    elif "url" not in columns:
        logger.info("Migrating database: adding missing 'url' column...")
        cursor.execute("ALTER TABLE audit_logs ADD COLUMN url TEXT NOT NULL DEFAULT ''")
        
    conn.commit()
    conn.close()


def load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            content = f.read()
        
        pattern = re.compile(r'\$\{([^}^{]+)\}')
        def replace_env(match):
            env_var = match.group(1)
            val = os.environ.get(env_var, "")
            if not val:
                logger.warning(f"Environment variable '{env_var}' is empty or not set.")
            return val
            
        resolved_content = pattern.sub(replace_env, content)
        return json.loads(resolved_content)
    except Exception as e:
        logger.error(f"Failed to load configuration from {CONFIG_FILE}: {e}")
        sys.exit(1)


def hydrate_initial_states(targets):
    global previous_states
    if not os.path.exists(DB_FILE):
        return
        
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
        cursor = conn.cursor()
        for target in targets:
            name = target["name"]
            cursor.execute("""
                SELECT status FROM audit_logs 
                WHERE target_name = ? 
                ORDER BY id DESC LIMIT 1
            """, (name,))
            row = cursor.fetchone()
            if row:
                previous_states[name] = row[0]
                logger.info(f"Hydrated state for '{name}': last known status was {row[0]}")
        conn.close()
    except Exception as e:
        logger.error(f"Failed to hydrate initial states from database: {e}")


def check_ssl_expiry(url: str):
    """Check SSL certificate expiration days remaining for HTTPS endpoints."""
    if not url.startswith("https://"):
        return None
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        port = parsed.port or 443
        context = ssl.create_default_context()
        with socket.create_connection((hostname, port), timeout=3.0) as sock:
            with context.wrap_socket(sock, server_hostname=hostname) as ssock:
                cert = ssock.getpeercert()
                not_after = cert.get('notAfter')
                expiry_date = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z")
                remaining_days = (expiry_date - datetime.utcnow()).days
                return remaining_days
    except Exception:
        return None


async def send_webhook(webhook_url: str, target_name: str, status: str, details: str):
    if not webhook_url:
        return
    
    color = 65280 if status == "UP" else 16711680
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
    min_ssl_days = target.get("min_ssl_days", 7)  # Warn if SSL expires in under 7 days

    start_time = asyncio.get_event_loop().time()
    status = "UP"
    status_code = None
    error_msg = None

    try:
        # Perform HTTP check
        response = await client.get(url, timeout=timeout, follow_redirects=True)
        latency = (asyncio.get_event_loop().time() - start_time) * 1000
        status_code = response.status_code

        if status_code != expected_status:
            status = "DEGRADED"
            error_msg = f"Expected status {expected_status}, got {status_code}"
        
        # Perform SSL Expiry Audit if HTTPS
        ssl_days = check_ssl_expiry(url)
        if ssl_days is not None and ssl_days < min_ssl_days:
            status = "DEGRADED"
            ssl_err = f"SSL certificate expires in {ssl_days} days!"
            error_msg = f"{error_msg} | {ssl_err}" if error_msg else ssl_err

    except httpx.TimeoutException:
        latency = (asyncio.get_event_loop().time() - start_time) * 1000
        status = "DOWN"
        error_msg = "Request timed out"
    except Exception as e:
        latency = (asyncio.get_event_loop().time() - start_time) * 1000
        status = "DOWN"
        error_msg = str(e)

    global previous_states
    last_state = previous_states.get(name)
    if last_state and last_state != status:
        logger.warning(f"State transition detected for {name}: {last_state} -> {status}")
        detail_text = f"URL: {url}\nLatency: {latency:.2f}ms\nError: {error_msg or 'None'}"
        asyncio.create_task(send_webhook(webhook_url, name, status, detail_text))
    
    previous_states[name] = status

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = sqlite3.connect(DB_FILE, timeout=10.0)
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

    hydrate_initial_states(targets)

    logger.info(f"Initialized database at {DB_FILE}. Monitoring {len(targets)} targets every {interval}s (with SSL & DNS auditing)...")

    async with httpx.AsyncClient() as client:
        while True:
            tasks = [check_target(client, target, webhook_url) for target in targets]
            await asyncio.gather(*tasks)
            await asyncio.sleep(interval)


def show_analytics():
    if not os.path.exists(DB_FILE):
        print("No audit database found (`infra_pulse.db`). Run the monitor first to collect metrics.")
        return

    conn = sqlite3.connect(DB_FILE, timeout=10.0)
    cursor = conn.cursor()

    cursor.execute("SELECT DISTINCT target_name FROM audit_logs")
    targets = [row[0] for row in cursor.fetchall()]

    if not targets:
        print("No monitoring logs found in the database yet.")
        conn.close()
        return

    print("\n" + "=" * 65)
    print("📊 INFRAPULSE MONITORING ANALYTICS REPORT")
    print("=" * 65)

    for name in targets:
        cursor.execute("SELECT COUNT(*) FROM audit_logs WHERE target_name = ?", (name,))
        total_checks = cursor.fetchone()[0]

        cursor.execute("SELECT COUNT(*) FROM audit_logs WHERE target_name = ? AND status = 'UP'", (name,))
        up_checks = cursor.fetchone()[0]

        uptime_pct = (up_checks / total_checks * 100) if total_checks > 0 else 0.0

        cursor.execute("SELECT AVG(latency_ms), MIN(latency_ms), MAX(latency_ms) FROM audit_logs WHERE target_name = ?", (name,))
        lat_avg, lat_min, lat_max = cursor.fetchone()

        cursor.execute("SELECT status, timestamp, error_message FROM audit_logs WHERE target_name = ? ORDER BY id DESC LIMIT 1", (name,))
        last_status, last_time, last_err = cursor.fetchone()

        print(f"\nTarget: {name}")
        print(f"  • Current Status : {last_status} (As of {last_time} UTC)")
        print(f"  • Uptime         : {uptime_pct:.2f}% ({up_checks}/{total_checks} checks successful)")
        print(f"  • Latency (ms)   : Avg: {lat_avg:.2f}ms | Min: {lat_min:.2f}ms | Max: {lat_max:.2f}ms")
        if last_err:
            print(f"  • Last Error     : {last_err}")

    print("\n" + "=" * 65 + "\n")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="InfraPulse Monitor & Analytics Engine")
    parser.add_argument("--analytics", action="store_true", help="Display historical uptime and latency analytics report")
    args = parser.parse_args()

    if args.analytics:
        show_analytics()
    else:
        try:
            asyncio.run(monitor_loop())
        except KeyboardInterrupt:
            logger.info("InfraPulse Monitor stopped by user.")


if __name__ == "__main__":
    main()
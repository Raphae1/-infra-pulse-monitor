# ⚡ InfraPulse Monitor

A lightweight, automated infrastructure health checker and logging utility built with Python, SQLite, and Docker to monitor service availability and response latency across endpoints and APIs.

---

## ✨ Features

- **Configuration-Driven:** Easily manage endpoints, timeouts, and check intervals via a `targets.json` file.
- **Async Concurrency:** Executes lightweight asynchronous pings using Python's `asyncio` and `httpx`.
- **Persistent Data Logging:** Captures timestamps, HTTP status codes, latency (ms), and error descriptions into a local SQLite database.
- **Dockerized Deployment:** Portable and container ready with simple volume mounting for database persistence.

---

## 🛠️ Tech Stack

- **Language:** Python 3.11 (`asyncio`, `httpx`)
- **Database:** SQLite3
- **Configuration:** JSON
- **Containerization:** Docker

---

## 🚀 Quickstart

### Running Locally

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/Raphae1/-infra-pulse-monitor.git](https://github.com/Raphae1/-infra-pulse-monitor.git)
   cd -infra-pulse-monitor
import http.server
import json
import os
import sqlite3
import sys
from urllib.parse import urlparse

PORT = 8080
DB_FILE = "infra_pulse.db"

class InfraPulseDashboardHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed_path = urlparse(self.path)
        
        if parsed_path.path == "/":
            self.serve_dashboard()
        elif parsed_path.path == "/api/metrics":
            self.serve_api_metrics()
        else:
            self.send_error(404, "Page Not Found")

    def serve_dashboard(self):
        html_content = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>InfraPulse | System Status Dashboard</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <meta http-equiv="refresh" content="30">
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen font-sans">
    <div class="max-w-6xl mx-auto px-4 py-8">
        <!-- Header -->
        <header class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-slate-800 pb-6 mb-8 gap-4">
            <div>
                <div class="flex items-center gap-3">
                    <span class="inline-block w-3 h-3 rounded-full bg-emerald-500 animate-pulse"></span>
                    <h1 class="text-2xl font-bold tracking-tight text-white">InfraPulse Monitor</h1>
                </div>
                <p class="text-sm text-slate-400 mt-1">Real-time infrastructure observability & health analytics</p>
            </div>
            <div class="flex items-center gap-4 text-xs text-slate-400 bg-slate-900 px-4 py-2 rounded-lg border border-slate-800">
                <span>Auto-refreshing every 30s</span>
                <span class="text-slate-600">|</span>
                <span id="utc-time"></span>
            </div>
        </header>

        <!-- Metrics Grid -->
        <div id="targets-container" class="grid grid-cols-1 md:grid-cols-2 gap-6">
            <!-- Dynamic Content Injected Here -->
            <div class="col-span-full text-center py-12 text-slate-500">Loading monitoring metrics...</div>
        </div>
    </div>

    <script>
        document.getElementById('utc-time').innerText = new Date().toUTCString();

        async function fetchMetrics() {
            try {
                const response = await fetch('/api/metrics');
                const data = await response.json();
                const container = document.getElementById('targets-container');
                
                if (data.length === 0) {
                    container.innerHTML = '<div class="col-span-full text-center py-12 text-slate-500 bg-slate-900 rounded-xl border border-slate-800">No monitoring audit logs found in database. Run `python monitor.py` to collect data.</div>';
                    return;
                }

                container.innerHTML = data.map(target => {
                    const isUp = target.status === 'UP';
                    const badgeColor = isUp ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border-rose-500/20';
                    const dotColor = isUp ? 'bg-emerald-500' : 'bg-rose-500';

                    return `
                        <div class="bg-slate-900 border border-slate-800 rounded-xl p-6 shadow-xl flex flex-col justify-between">
                            <div>
                                <div class="flex justify-between items-start mb-4">
                                    <div>
                                        <h2 class="text-lg font-semibold text-white">${target.name}</h2>
                                        <a href="${target.url}" target="_blank" class="text-xs text-slate-400 hover:text-indigo-400 truncate block max-w-md mt-0.5">${target.url}</a>
                                    </div>
                                    <span class="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-xs font-medium border ${badgeColor}">
                                        <span class="w-1.5 h-1.5 rounded-full ${dotColor}"></span>
                                        ${target.status}
                                    </span>
                                </div>

                                <div class="grid grid-cols-3 gap-4 my-6 bg-slate-950 p-4 rounded-lg border border-slate-800/60">
                                    <div>
                                        <div class="text-xs text-slate-400 font-medium">Uptime</div>
                                        <div class="text-lg font-bold text-white mt-1">${target.uptime_pct.toFixed(1)}%</div>
                                    </div>
                                    <div>
                                        <div class="text-xs text-slate-400 font-medium">Avg Latency</div>
                                        <div class="text-lg font-bold text-white mt-1">${target.lat_avg ? target.lat_avg.toFixed(0) : 0} ms</div>
                                    </div>
                                    <div>
                                        <div class="text-xs text-slate-400 font-medium">Total Checks</div>
                                        <div class="text-lg font-bold text-white mt-1">${target.total_checks}</div>
                                    </div>
                                </div>
                            </div>

                            <div class="flex justify-between items-center text-xs text-slate-400 border-t border-slate-800 pt-4 mt-2">
                                <span>Last Checked: ${target.last_time || 'N/A'} UTC</span>
                                <span class="${target.error ? 'text-rose-400' : 'text-slate-400'}">${target.error ? 'Error: ' + target.error : 'Healthy'}</span>
                            </div>
                        </div>
                    `;
                }).join('');
            } catch (err) {
                console.error("Failed to load metrics:", err);
            }
        }

        fetchMetrics();
        setInterval(fetchMetrics, 10000);
    </script>
</body>
</html>"""
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(html_content.encode("utf-8"))

    def serve_api_metrics(self):
        if not os.path.exists(DB_FILE):
            self.send_json([])
            return

        try:
            conn = sqlite3.connect(DB_FILE, timeout=5.0)
            cursor = conn.cursor()
            cursor.execute("SELECT DISTINCT target_name FROM audit_logs")
            targets = [row[0] for row in cursor.fetchall()]

            metrics = []
            for name in targets:
                cursor.execute("SELECT COUNT(*) FROM audit_logs WHERE target_name = ?", (name,))
                total_checks = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM audit_logs WHERE target_name = ? AND status = 'UP'", (name,))
                up_checks = cursor.fetchone()[0]

                uptime_pct = (up_checks / total_checks * 100) if total_checks > 0 else 0.0

                cursor.execute("SELECT AVG(latency_ms), MIN(latency_ms), MAX(latency_ms) FROM audit_logs WHERE target_name = ?", (name,))
                lat_avg, lat_min, lat_max = cursor.fetchone()

                cursor.execute("SELECT status, timestamp, error_message, url FROM audit_logs WHERE target_name = ? ORDER BY id DESC LIMIT 1", (name,))
                row = cursor.fetchone()
                last_status = row[0] if row else "UNKNOWN"
                last_time = row[1] if row else ""
                last_err = row[2] if row else ""
                url = row[3] if row else ""

                metrics.append({
                    "name": name,
                    "url": url,
                    "status": last_status,
                    "uptime_pct": uptime_pct,
                    "total_checks": total_checks,
                    "lat_avg": lat_avg,
                    "lat_min": lat_min,
                    "lat_max": lat_max,
                    "last_time": last_time,
                    "error": last_err
                })

            conn.close()
            self.send_json(metrics)
        except Exception as e:
            print(f"API Error: {e}")
            self.send_json([])

    def send_json(self, data):
        payload = json.dumps(data).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(payload)

def run():
    server_address = ("", PORT)
    httpd = http.server.HTTPServer(server_address, InfraPulseDashboardHandler)
    print(f"🚀 InfraPulse Web Dashboard running at http://localhost:{PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped by user.")

if __name__ == "__main__":
    run()
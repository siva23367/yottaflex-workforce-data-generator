"""Local web dashboard for the YottaFlex synthetic workforce generator."""
from __future__ import annotations
import json
import subprocess
import sys
from pathlib import Path
from threading import Lock
from flask import Flask, jsonify, render_template, request, send_from_directory, abort

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "data" / "web_generated"
ALLOWED_FILES = {
    "Employee.csv", "Team.csv", "Skill.csv", "ShiftTemplate.csv",
    "EmployeeSkill.csv", "ShiftAssignment.csv", "ShiftAssignmentEvent.csv",
    "LeaveRequest.csv", "HolidayCalendar.csv", "LeaveRequestEvent.csv",
    "AttendanceEvent.csv", "CoverageRequirement.csv", "CoverageGap.csv",
    "SwapRequest.csv", "SwapRequestEvent.csv", "OvertimeSummary.csv",
    "Employee.json", "Team.json", "Skill.json", "ShiftTemplate.json",
    "EmployeeSkill.json", "ShiftAssignment.json", "ShiftAssignmentEvent.json",
    "LeaveRequest.json", "HolidayCalendar.json", "LeaveRequestEvent.json",
    "AttendanceEvent.json", "CoverageRequirement.json", "CoverageGap.json",
    "SwapRequest.json", "SwapRequestEvent.json", "OvertimeSummary.json",
    "manifest.json",
    "workforce_seed.sqlite", "workforce_seed.sql",
    "ground_truth/ground_truth.csv", "ground_truth/ground_truth.json",
}
app = Flask(__name__)
_generation_lock = Lock()
_last_result = None


def read_manifest():
    path = OUTPUT / "manifest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


@app.get("/")
def index():
    return render_template("index.html", result=read_manifest())


@app.get("/api/status")
def status():
    manifest = read_manifest()
    return jsonify({"generated": bool(manifest), "manifest": manifest})


@app.post("/api/generate")
def generate():
    global _last_result
    payload = request.get_json(silent=True) or {}
    try:
        seed = int(payload.get("seed", 20261022))
        employees = int(payload.get("employees", 2000))
        teams = int(payload.get("teams", 100))
        days = int(payload.get("days", 90))
        leaves = int(payload.get("leaves", 5000))
        anomaly_rate = float(payload.get("anomaly_rate", 0.01))
        if not (1 <= teams and max(2, teams) <= employees <= 10000):
            raise ValueError("Use 1 ≤ teams, max(2, teams) ≤ employees ≤ 10,000.")
        if not (1 <= days <= 365):
            raise ValueError("Days must be between 1 and 365.")
        if not (0 <= leaves <= 100000):
            raise ValueError("Leave requests must be between 0 and 100,000.")
        if not (0 <= anomaly_rate <= 0.05):
            raise ValueError("Anomaly rate must be between 0 and 0.05 (5%).")
    except (TypeError, ValueError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    if not _generation_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "A generation job is already running."}), 409
    try:
        OUTPUT.mkdir(parents=True, exist_ok=True)
        cmd = [sys.executable, str(ROOT / "generate.py"), "--seed", str(seed),
               "--employees", str(employees), "--teams", str(teams), "--days", str(days),
               "--leaves", str(leaves), "--anomaly-rate", str(anomaly_rate), "--out", str(OUTPUT)]
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
        if proc.returncode != 0:
            return jsonify({"ok": False, "error": (proc.stderr or proc.stdout)[-5000:]}), 500
        result = json.loads(proc.stdout)
        manifest = read_manifest()
        if manifest is None:
            raise OSError("Generator completed without writing manifest.json")
        result["metrics"] = manifest["metrics"]
        result["coverage_gap_count"] = manifest["metrics"]["coverage_gap_count"]
        result["coverage_shortfall_total"] = manifest["metrics"][
            "coverage_shortfall_total"
        ]
        _last_result = result
        return jsonify({"ok": result.get("valid", False), "result": result,
                        "manifest": manifest, "files": sorted(ALLOWED_FILES)})
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Generation exceeded the 10-minute time limit."}), 504
    except (OSError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    finally:
        _generation_lock.release()


@app.get("/api/files")
def files():
    manifest = read_manifest()
    if not manifest:
        return jsonify({"files": []})
    found = []
    for name in sorted(ALLOWED_FILES):
        path = OUTPUT / name
        if path.is_file():
            found.append({"name": name, "size": path.stat().st_size})
    return jsonify({"files": found})


@app.get("/download/<path:filename>")
def download(filename):
    if filename not in ALLOWED_FILES:
        abort(404)
    target = OUTPUT / filename
    if not target.is_file():
        abort(404)
    return send_from_directory(OUTPUT, filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)

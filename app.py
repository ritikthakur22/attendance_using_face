"""Browser front end for the face-recognition attendance project.

Run locally with::

    python app.py

Then open http://127.0.0.1:5000 in a browser. Camera access works on
``localhost`` / ``127.0.0.1`` in modern browsers.
"""

from __future__ import annotations

import base64
import binascii
import csv
import io
import os
import secrets
import threading
import time
import uuid
import zipfile
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pandas as pd
from flask import (
    Flask,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    session,
    url_for,
)

import database as db

try:
    import face_recognition
except (ImportError, SystemExit):  # Some releases call quit() when model loading fails.
    face_recognition = None

BASE_DIR = Path(__file__).resolve().parent
REPORT_COLUMNS = [
    "student_id",
    "name",
    "class_name",
    "session_name",
    "date",
    "time",
    "status",
]
MAX_IMAGES_PER_REGISTRATION = 20
MAX_IMAGE_BYTES = 5 * 1024 * 1024
CACHE_SECONDS = 5.0
SCAN_TTL_SECONDS = 15 * 60

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("ATTENDANCE_SECRET_KEY", secrets.token_hex(32)),
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,
    JSON_SORT_KEYS=False,
)

_cache_lock = threading.Lock()
_encoding_cache: dict[str, tuple[float, list[np.ndarray], list[str], dict[str, str]]] = {}
_scan_lock = threading.Lock()
_scan_states: dict[str, dict[str, Any]] = {}


def _csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


@app.context_processor
def inject_template_globals() -> dict[str, Any]:
    return {
        "csrf_token": _csrf_token(),
        "today_iso": date.today().isoformat(),
        "face_engine_ready": face_recognition is not None,
    }


def _require_csrf() -> None:
    supplied = request.headers.get("X-CSRF-Token")
    if supplied is None:
        supplied = request.form.get("csrf_token")
    expected = session.get("csrf_token")
    if not expected or not supplied or not secrets.compare_digest(expected, supplied):
        abort(400, description="Invalid or missing form token. Refresh the page and try again.")


@app.before_request
def protect_unsafe_requests() -> None:
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        _require_csrf()


def _face_engine_or_error():
    if face_recognition is None:
        raise RuntimeError(
            "The face_recognition package is not installed. Activate the project virtual "
            "environment and run: pip install -r requirements.txt"
        )
    return face_recognition


def _decode_image(data_url: str) -> np.ndarray:
    if not isinstance(data_url, str) or not data_url:
        raise ValueError("An image is required.")
    try:
        _, encoded = data_url.split(",", 1) if "," in data_url else ("", data_url)
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("The submitted image is not valid base64 data.") from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("Each image must be between 1 byte and 5 MB.")
    array = np.frombuffer(raw, dtype=np.uint8)
    frame = cv2.imdecode(array, cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError("The submitted image could not be decoded.")
    return frame


def _single_face_encoding(frame: np.ndarray) -> np.ndarray:
    engine = _face_engine_or_error()
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    locations = engine.face_locations(rgb, model="hog")
    if len(locations) == 0:
        raise ValueError("No face was detected in one of the images.")
    if len(locations) > 1:
        raise ValueError("Every registration image must contain exactly one face.")
    encodings = engine.face_encodings(rgb, known_face_locations=locations)
    if not encodings:
        raise ValueError("A detected face could not be encoded.")
    return encodings[0]


def _invalidate_encoding_cache() -> None:
    with _cache_lock:
        _encoding_cache.clear()


def _known_faces(class_name: str | None) -> tuple[list[np.ndarray], list[str], dict[str, str]]:
    key = (class_name or "").strip()
    now = time.monotonic()
    with _cache_lock:
        cached = _encoding_cache.get(key)
        if cached and now - cached[0] < CACHE_SECONDS:
            return cached[1], cached[2], cached[3]
    values = db.load_all_encodings(key or None)
    with _cache_lock:
        _encoding_cache[key] = (now, values[0], values[1], values[2])
    return values


def _purge_scan_states(now: float) -> None:
    expired = [
        scan_id
        for scan_id, state in _scan_states.items()
        if now - float(state.get("updated_at", 0)) > SCAN_TTL_SECONDS
    ]
    for scan_id in expired:
        _scan_states.pop(scan_id, None)


def _scan_state(
    scan_id: str,
    session_name: str,
    class_name: str,
    tolerance: float,
    confirm_frames: int,
) -> dict[str, Any]:
    now = time.monotonic()
    with _scan_lock:
        _purge_scan_states(now)
        state = _scan_states.get(scan_id)
        signature = (session_name, class_name, tolerance, confirm_frames)
        if state is None or state.get("signature") != signature:
            state = {
                "signature": signature,
                "counts": defaultdict(int),
                "updated_at": now,
            }
            _scan_states[scan_id] = state
        state["updated_at"] = now
        return state


def _validated_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except ValueError as exc:
        raise ValueError("Date must use YYYY-MM-DD format.") from exc


def _safe_download_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)
    return cleaned.strip("._") or "attendance"


def _rows_to_dataframe(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=REPORT_COLUMNS)


def _report_rows(scope: str, target_date: str | None, session_name: str | None, class_name: str | None):
    if scope == "all":
        return db.get_attendance_filtered(None, session_name, class_name)
    actual_date = target_date or date.today().isoformat()
    return db.get_attendance_filtered(actual_date, session_name, class_name)


@app.errorhandler(400)
def bad_request(error):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": getattr(error, "description", "Bad request.")}), 400
    return error


@app.errorhandler(413)
def request_too_large(_error):
    if request.path.startswith("/api/"):
        return jsonify({"ok": False, "error": "The upload is too large."}), 413
    return "The upload is too large.", 413


@app.route("/")
def dashboard():
    students = db.get_students_with_encoding_counts()
    today_rows = db.get_attendance_by_date()
    recent = db.get_recent_attendance(10)
    sessions_today = sorted({row[3] for row in today_rows})
    return render_template(
        "dashboard.html",
        page="dashboard",
        student_count=len(students),
        secure_encoding_count=sum(row[3] for row in students),
        present_today=len(today_rows),
        session_count=len(sessions_today),
        recent=recent,
        classes=db.get_classes(),
    )


@app.route("/students")
def students_page():
    selected_class = request.args.get("class", "").strip()
    students = db.get_students_with_encoding_counts(selected_class or None)
    return render_template(
        "students.html",
        page="students",
        students=students,
        classes=db.get_classes(),
        selected_class=selected_class,
    )


@app.route("/students/register")
def register_page():
    return render_template(
        "register.html",
        page="register",
        classes=db.get_classes(),
    )


@app.post("/api/students/register")
def register_student_api():
    try:
        payload = request.get_json(force=True)
        student_id = str(payload.get("student_id", "")).strip()
        name = str(payload.get("name", "")).strip()
        class_name = str(payload.get("class_name", "")).strip()
        add_images = bool(payload.get("add_images", False))
        images = payload.get("images")

        if not student_id or not name:
            raise ValueError("Student ID and name are required.")
        if not isinstance(images, list) or not images:
            raise ValueError("Capture or select at least one image.")
        if len(images) > MAX_IMAGES_PER_REGISTRATION:
            raise ValueError(f"A maximum of {MAX_IMAGES_PER_REGISTRATION} images is allowed.")

        existing = db.get_student(student_id)
        if existing:
            if not add_images:
                raise ValueError(
                    "That student ID already exists. Select 'add images' only when this is "
                    "the same student."
                )
            _, stored_name, stored_class = existing
            if (stored_name, stored_class) != (name, class_name):
                raise ValueError(
                    "The supplied name or class does not match the existing student record."
                )

        encodings = [_single_face_encoding(_decode_image(image)) for image in images]
        created = False
        if not existing:
            if not db.add_student(student_id, name, class_name):
                raise RuntimeError("The student ID was created by another request. Try again.")
            created = True
        try:
            saved_count = db.save_encodings(student_id, encodings)
        except Exception:
            if created:
                db.delete_student(student_id)
            raise

        _invalidate_encoding_cache()
        return jsonify(
            {
                "ok": True,
                "message": f"Saved {saved_count} face image(s) for {name} ({student_id}).",
                "student_id": student_id,
                "saved_count": saved_count,
            }
        )
    except (ValueError, RuntimeError, db.DatabaseError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/students/<student_id>/delete")
def delete_student(student_id: str):
    deleted = db.delete_student(student_id)
    _invalidate_encoding_cache()
    if deleted:
        flash(f"Student {student_id} and related records were deleted.", "success")
    else:
        flash(f"Student {student_id} was not found.", "warning")
    return redirect(url_for("students_page"))


@app.route("/students/<student_id>/edit", methods=["GET", "POST"])
def edit_student_page(student_id: str):
    student = db.get_student(student_id)
    if not student:
        flash(f"Student {student_id} was not found.", "warning")
        return redirect(url_for("students_page"))
    
    if request.method == "POST":
        new_id = request.form.get("student_id", "").strip()
        new_name = request.form.get("name", "").strip()
        new_class = request.form.get("class_name", "").strip()
        
        if not new_id:
            flash("Student ID cannot be empty.", "danger")
        elif not new_name:
            flash("Student name cannot be empty.", "danger")
        else:
            try:
                if db.update_student(student_id, new_id, new_name, new_class):
                    flash("Student updated successfully.", "success")
                    _invalidate_encoding_cache()
                    return redirect(url_for("students_page"))
                else:
                    flash("Failed to update student.", "danger")
            except ValueError as e:
                flash(str(e), "danger")
                
    # Refresh student data in case of error
    student = db.get_student(student_id)
    return render_template(
        "edit_student.html",
        page="students",
        student=student,
    )


@app.route("/attendance")
def attendance_page():
    return render_template(
        "attendance.html",
        page="attendance",
        classes=db.get_classes(),
        sessions=db.get_sessions(20),
        scan_id=str(uuid.uuid4()),
    )


@app.post("/api/attendance/recognize")
def recognize_attendance_api():
    try:
        payload = request.get_json(force=True)
        image = _decode_image(payload.get("image", ""))
        session_name = str(payload.get("session_name", "")).strip()
        class_name = str(payload.get("class_name", "")).strip()
        scan_id = str(payload.get("scan_id", "")).strip()
        tolerance = float(payload.get("tolerance", 0.5))
        confirm_frames = int(payload.get("confirm_frames", 3))

        if not session_name:
            raise ValueError("A session name is required.")
        if not scan_id or len(scan_id) > 100:
            raise ValueError("The scan identifier is invalid. Refresh the page.")
        if not 0.1 <= tolerance <= 0.9:
            raise ValueError("Tolerance must be between 0.1 and 0.9.")
        if not 1 <= confirm_frames <= 20:
            raise ValueError("Confirmation frames must be between 1 and 20.")

        engine = _face_engine_or_error()
        known_encodings, known_ids, id_to_name = _known_faces(class_name or None)
        if not known_encodings:
            scope = f" for class {class_name}" if class_name else ""
            raise ValueError(f"No registered face encodings were found{scope}.")

        rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        locations = engine.face_locations(rgb, model="hog")
        encodings = engine.face_encodings(rgb, locations)
        state = _scan_state(scan_id, session_name, class_name, tolerance, confirm_frames)
        counts: defaultdict[str, int] = state["counts"]
        attendance_rows = db.get_attendance_filtered(
            target_date=date.today().isoformat(),
            session_name=session_name,
            class_name=class_name or None,
        )
        already_present = {row[0] for row in attendance_rows}

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        newly_marked: list[dict[str, str]] = []
        for location, encoding in zip(locations, encodings):
            top, right, bottom, left = [int(value) for value in location]
            result: dict[str, Any] = {
                "box": {"top": top, "right": right, "bottom": bottom, "left": left},
                "name": "Unknown",
                "student_id": None,
                "distance": None,
                "progress": 0,
                "status": "unknown",
            }
            distances = engine.face_distance(known_encodings, encoding)
            if len(distances):
                best_index = int(np.argmin(distances))
                best_distance = float(distances[best_index])
                result["distance"] = round(best_distance, 3)
                if best_distance <= tolerance:
                    student_id = known_ids[best_index]
                    name = id_to_name[student_id]
                    result.update(student_id=student_id, name=name)
                    seen.add(student_id)
                    if student_id in already_present:
                        counts[student_id] = confirm_frames
                        result.update(progress=confirm_frames, status="present")
                    else:
                        counts[student_id] += 1
                        progress = min(counts[student_id], confirm_frames)
                        result.update(progress=progress, status="confirming")
                        if progress >= confirm_frames:
                            inserted = db.mark_attendance(student_id, session_name=session_name)
                            already_present.add(student_id)
                            result["status"] = "present"
                            if inserted:
                                newly_marked.append(
                                    {"student_id": student_id, "name": name}
                                )
            results.append(result)

        for student_id in list(counts):
            if student_id not in seen and student_id not in already_present:
                counts[student_id] = 0

        return jsonify(
            {
                "ok": True,
                "faces": results,
                "newly_marked": newly_marked,
                "present_count": len(already_present),
                "registered_count": len(set(known_ids)),
                "attendance": [
                    dict(zip(REPORT_COLUMNS, row))
                    for row in db.get_attendance_filtered(
                        target_date=date.today().isoformat(),
                        session_name=session_name,
                        class_name=class_name or None,
                    )
                ],
            }
        )
    except (ValueError, RuntimeError, db.DatabaseError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.post("/api/attendance/manual")
def manual_attendance_api():
    try:
        payload = request.get_json(force=True)
        student_id = str(payload.get("student_id", "")).strip()
        session_name = str(payload.get("session_name", "")).strip()
        if not student_id or not session_name:
            raise ValueError("Student ID and session name are required.")
        inserted = db.mark_attendance(student_id, session_name=session_name)
        student = db.get_student(student_id)
        return jsonify(
            {
                "ok": True,
                "inserted": inserted,
                "message": (
                    f"Marked {student[1]} present."
                    if inserted
                    else f"{student[1]} was already present in this session today."
                ),
            }
        )
    except (ValueError, db.DatabaseError) as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@app.get("/api/attendance/current")
def current_attendance_api():
    session_name = request.args.get("session", "").strip()
    target_date = request.args.get("date", date.today().isoformat()).strip()
    try:
        target_date = _validated_date(target_date) or date.today().isoformat()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    rows = db.get_attendance_by_date(target_date, session_name or None)
    return jsonify(
        {
            "ok": True,
            "attendance": [dict(zip(REPORT_COLUMNS, row)) for row in rows],
        }
    )


@app.route("/reports")
def reports_page():
    try:
        selected_date = _validated_date(request.args.get("date")) or date.today().isoformat()
    except ValueError:
        selected_date = date.today().isoformat()
    selected_session = request.args.get("session", "").strip()
    selected_class = request.args.get("class", "").strip()
    scope = request.args.get("scope", "date")
    if scope not in {"date", "all"}:
        scope = "date"
    rows = _report_rows(
        scope,
        selected_date if scope == "date" else None,
        selected_session or None,
        selected_class or None,
    )
    return render_template(
        "reports.html",
        page="reports",
        rows=rows,
        classes=db.get_classes(),
        sessions=db.get_sessions(),
        selected_date=selected_date,
        selected_session=selected_session,
        selected_class=selected_class,
        scope=scope,
    )


@app.get("/reports/export")
def export_report_download():
    try:
        scope = request.args.get("scope", "date")
        if scope not in {"date", "all"}:
            raise ValueError("Invalid report scope.")
        target_date = _validated_date(request.args.get("date"))
        session_name = request.args.get("session", "").strip() or None
        class_name = request.args.get("class", "").strip() or None
        output_format = request.args.get("format", "csv")
        if output_format not in {"csv", "xlsx", "both"}:
            raise ValueError("Invalid report format.")
        rows = _report_rows(scope, target_date, session_name, class_name)
        if not rows:
            flash("No attendance records matched the selected filters.", "warning")
            return redirect(url_for("reports_page"))

        df = _rows_to_dataframe(rows)
        suffix_parts = ["all" if scope == "all" else (target_date or date.today().isoformat())]
        if session_name:
            suffix_parts.append(session_name)
        if class_name:
            suffix_parts.append(class_name)
        stem = _safe_download_name("attendance_" + "_".join(suffix_parts))

        if output_format == "csv":
            buffer = io.StringIO()
            df.to_csv(buffer, index=False, quoting=csv.QUOTE_MINIMAL)
            data = io.BytesIO(buffer.getvalue().encode("utf-8-sig"))
            return send_file(
                data,
                as_attachment=True,
                download_name=f"{stem}.csv",
                mimetype="text/csv; charset=utf-8",
            )

        if output_format == "xlsx":
            data = io.BytesIO()
            with pd.ExcelWriter(data, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Attendance")
                if scope == "all":
                    summary = (
                        df.groupby(
                            ["student_id", "name", "class_name", "session_name"],
                            dropna=False,
                        )
                        .agg(
                            days_present=("date", "nunique"),
                            attendance_records=("date", "size"),
                        )
                        .reset_index()
                    )
                    summary.to_excel(writer, index=False, sheet_name="Summary")
            data.seek(0)
            return send_file(
                data,
                as_attachment=True,
                download_name=f"{stem}.xlsx",
                mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            csv_buffer = io.StringIO()
            df.to_csv(csv_buffer, index=False)
            bundle.writestr(f"{stem}.csv", csv_buffer.getvalue().encode("utf-8-sig"))
            xlsx_buffer = io.BytesIO()
            with pd.ExcelWriter(xlsx_buffer, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name="Attendance")
                if scope == "all":
                    summary = (
                        df.groupby(
                            ["student_id", "name", "class_name", "session_name"],
                            dropna=False,
                        )
                        .agg(
                            days_present=("date", "nunique"),
                            attendance_records=("date", "size"),
                        )
                        .reset_index()
                    )
                    summary.to_excel(writer, index=False, sheet_name="Summary")
            bundle.writestr(f"{stem}.xlsx", xlsx_buffer.getvalue())
        archive.seek(0)
        return send_file(
            archive,
            as_attachment=True,
            download_name=f"{stem}.zip",
            mimetype="application/zip",
        )
    except (ValueError, db.DatabaseError) as exc:
        flash(str(exc), "danger")
        return redirect(url_for("reports_page"))


def main() -> None:
    db.init_db()
    app.run(host="127.0.0.1", port=5000, debug=True)


if __name__ == "__main__":
    main()

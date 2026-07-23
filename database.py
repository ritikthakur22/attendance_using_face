"""SQLite persistence for the face-recognition attendance project.

Face encodings are stored as NumPy ``.npy`` byte streams and loaded with
``allow_pickle=False``. Legacy pickle encodings are deliberately not loaded.
"""

from __future__ import annotations

import io
import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from typing import Iterable

import numpy as np

DB_PATH = os.path.join(os.path.dirname(__file__), "attendance.db")


class DatabaseError(RuntimeError):
    """Base exception for project-specific database failures."""


class StudentNotFoundError(DatabaseError):
    """Raised when an operation references an unknown student ID."""


def get_connection() -> sqlite3.Connection:
    """Open a raw SQLite connection. Callers are responsible for closing it."""
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


@contextmanager
def connection():
    """Yield a connection, committing on success and always closing it."""
    conn = get_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _migrate_face_encoding_table(conn: sqlite3.Connection) -> None:
    columns = _column_names(conn, "face_encodings")
    if "encoding_format" not in columns:
        # Existing rows came from the old pickle-based implementation. Mark
        # them as legacy so they are never unpickled automatically.
        conn.execute(
            "ALTER TABLE face_encodings "
            "ADD COLUMN encoding_format TEXT NOT NULL DEFAULT 'legacy_pickle'"
        )


def _migrate_attendance_table(conn: sqlite3.Connection) -> None:
    columns = _column_names(conn, "attendance")
    if not columns or "session_name" in columns:
        return

    conn.execute("ALTER TABLE attendance RENAME TO attendance_legacy")
    conn.execute(
        """
        CREATE TABLE attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id TEXT NOT NULL,
            name TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            session_name TEXT NOT NULL DEFAULT 'default',
            status TEXT NOT NULL DEFAULT 'Present',
            FOREIGN KEY (student_id) REFERENCES students (student_id) ON DELETE CASCADE,
            UNIQUE(student_id, date, session_name)
        )
        """
    )
    conn.execute(
        """
        INSERT INTO attendance (id, student_id, name, date, time, session_name, status)
        SELECT id, student_id, name, date, time, 'default', status
        FROM attendance_legacy
        """
    )
    conn.execute("DROP TABLE attendance_legacy")


def init_db() -> None:
    """Create tables and safely migrate databases from the original project."""
    with connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS students (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT UNIQUE NOT NULL,
                name TEXT NOT NULL,
                class_name TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS face_encodings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                encoding BLOB NOT NULL,
                encoding_format TEXT NOT NULL DEFAULT 'npy',
                FOREIGN KEY (student_id) REFERENCES students (student_id) ON DELETE CASCADE
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS attendance (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_id TEXT NOT NULL,
                name TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                session_name TEXT NOT NULL DEFAULT 'default',
                status TEXT NOT NULL DEFAULT 'Present',
                FOREIGN KEY (student_id) REFERENCES students (student_id) ON DELETE CASCADE,
                UNIQUE(student_id, date, session_name)
            )
            """
        )
        _migrate_face_encoding_table(conn)
        _migrate_attendance_table(conn)


# ---------------- Student management ----------------

def add_student(student_id: str, name: str, class_name: str = "") -> bool:
    student_id = student_id.strip()
    name = name.strip()
    class_name = class_name.strip()
    if not student_id or not name:
        raise ValueError("student_id and name must not be empty")

    try:
        with connection() as conn:
            conn.execute(
                "INSERT INTO students (student_id, name, class_name) VALUES (?, ?, ?)",
                (student_id, name, class_name),
            )
        return True
    except sqlite3.IntegrityError as exc:
        if "students.student_id" in str(exc) or "UNIQUE constraint failed" in str(exc):
            return False
        raise DatabaseError("Could not add student") from exc


def get_student(student_id: str) -> tuple[str, str, str] | None:
    with connection() as conn:
        row = conn.execute(
            "SELECT student_id, name, class_name FROM students WHERE student_id = ?",
            (student_id.strip(),),
        ).fetchone()
    return row


def student_exists(student_id: str) -> bool:
    return get_student(student_id) is not None


def get_all_students(class_name: str | None = None) -> list[tuple[str, str, str]]:
    with connection() as conn:
        if class_name is None:
            rows = conn.execute(
                "SELECT student_id, name, class_name FROM students ORDER BY student_id"
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT student_id, name, class_name
                FROM students
                WHERE class_name = ?
                ORDER BY student_id
                """,
                (class_name.strip(),),
            ).fetchall()
    return rows


# ---------------- Face encodings ----------------

def _serialize_encoding(encoding: np.ndarray) -> bytes:
    array = np.asarray(encoding, dtype=np.float64)
    if array.shape != (128,):
        raise ValueError(f"Expected a 128-value face encoding, got shape {array.shape}")
    buffer = io.BytesIO()
    np.save(buffer, array, allow_pickle=False)
    return buffer.getvalue()


def _deserialize_encoding(blob: bytes) -> np.ndarray:
    array = np.load(io.BytesIO(blob), allow_pickle=False)
    array = np.asarray(array, dtype=np.float64)
    if array.shape != (128,):
        raise ValueError(f"Stored encoding has invalid shape {array.shape}")
    return array


def save_encoding(student_id: str, encoding: np.ndarray) -> None:
    student_id = student_id.strip()
    blob = _serialize_encoding(encoding)
    try:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO face_encodings (student_id, encoding, encoding_format)
                VALUES (?, ?, 'npy')
                """,
                (student_id, blob),
            )
    except sqlite3.IntegrityError as exc:
        if "FOREIGN KEY constraint failed" in str(exc):
            raise StudentNotFoundError(f"Unknown student ID: {student_id}") from exc
        raise DatabaseError("Could not save face encoding") from exc


def save_encodings(student_id: str, encodings: Iterable[np.ndarray]) -> int:
    blobs = [_serialize_encoding(encoding) for encoding in encodings]
    if not blobs:
        return 0

    try:
        with connection() as conn:
            conn.executemany(
                """
                INSERT INTO face_encodings (student_id, encoding, encoding_format)
                VALUES (?, ?, 'npy')
                """,
                [(student_id.strip(), blob) for blob in blobs],
            )
    except sqlite3.IntegrityError as exc:
        if "FOREIGN KEY constraint failed" in str(exc):
            raise StudentNotFoundError(f"Unknown student ID: {student_id}") from exc
        raise DatabaseError("Could not save face encodings") from exc
    return len(blobs)


def load_all_encodings(
    class_name: str | None = None,
) -> tuple[list[np.ndarray], list[str], dict[str, str]]:
    query = """
        SELECT fe.student_id, fe.encoding, fe.encoding_format, s.name
        FROM face_encodings fe
        JOIN students s ON fe.student_id = s.student_id
    """
    params: tuple[str, ...] = ()
    if class_name is not None:
        query += " WHERE s.class_name = ?"
        params = (class_name.strip(),)
    query += " ORDER BY fe.id"

    with connection() as conn:
        rows = conn.execute(query, params).fetchall()

    known_encodings: list[np.ndarray] = []
    known_ids: list[str] = []
    id_to_name: dict[str, str] = {}
    skipped_legacy = 0

    for student_id, blob, encoding_format, name in rows:
        if encoding_format != "npy":
            skipped_legacy += 1
            continue
        try:
            encoding = _deserialize_encoding(blob)
        except (ValueError, OSError):
            continue
        known_encodings.append(encoding)
        known_ids.append(student_id)
        id_to_name[student_id] = name

    if skipped_legacy:
        print(
            f"Warning: skipped {skipped_legacy} legacy pickle encoding(s). "
            "Re-register those students to create secure encodings."
        )

    return known_encodings, known_ids, id_to_name


# ---------------- Attendance ----------------

def mark_attendance(
    student_id: str,
    session_name: str = "default",
    status: str = "Present",
    attendance_date: str | None = None,
    attendance_time: str | None = None,
) -> bool:
    """Mark a student once for a date/session.

    Returns ``True`` for a new record and ``False`` only for an existing record
    with the same student, date, and session. Other integrity errors are raised.
    """
    student_id = student_id.strip()
    session_name = session_name.strip()
    status = status.strip()
    if not session_name:
        raise ValueError("session_name must not be empty")
    if not status:
        raise ValueError("status must not be empty")

    student = get_student(student_id)
    if student is None:
        raise StudentNotFoundError(f"Unknown student ID: {student_id}")
    _, name, _ = student

    target_date = attendance_date or date.today().isoformat()
    target_time = attendance_time or datetime.now().strftime("%H:%M:%S")

    try:
        with connection() as conn:
            conn.execute(
                """
                INSERT INTO attendance
                    (student_id, name, date, time, session_name, status)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (student_id, name, target_date, target_time, session_name, status),
            )
        return True
    except sqlite3.IntegrityError as exc:
        if "attendance.student_id, attendance.date, attendance.session_name" in str(exc):
            return False
        if "UNIQUE constraint failed" in str(exc):
            return False
        raise DatabaseError("Could not mark attendance") from exc


def get_attendance_by_date(
    target_date: str | None = None,
    session_name: str | None = None,
) -> list[tuple[str, str, str, str, str, str, str]]:
    target_date = target_date or date.today().isoformat()
    query = """
        SELECT a.student_id, a.name, s.class_name, a.session_name,
               a.date, a.time, a.status
        FROM attendance a
        JOIN students s ON a.student_id = s.student_id
        WHERE a.date = ?
    """
    params: list[str] = [target_date]
    if session_name is not None:
        query += " AND a.session_name = ?"
        params.append(session_name.strip())
    query += " ORDER BY a.session_name, a.time, a.student_id"

    with connection() as conn:
        return conn.execute(query, params).fetchall()


def get_all_attendance(
    session_name: str | None = None,
) -> list[tuple[str, str, str, str, str, str, str]]:
    query = """
        SELECT a.student_id, a.name, s.class_name, a.session_name,
               a.date, a.time, a.status
        FROM attendance a
        JOIN students s ON a.student_id = s.student_id
    """
    params: tuple[str, ...] = ()
    if session_name is not None:
        query += " WHERE a.session_name = ?"
        params = (session_name.strip(),)
    query += " ORDER BY a.date, a.session_name, a.time, a.student_id"

    with connection() as conn:
        return conn.execute(query, params).fetchall()


# ---------------- Dashboard and web helpers ----------------

def delete_student(student_id: str) -> bool:
    """Delete a student and cascade their encodings and attendance records."""
    with connection() as conn:
        cursor = conn.execute(
            "DELETE FROM students WHERE student_id = ?",
            (student_id.strip(),),
        )
    return cursor.rowcount > 0


def update_student(old_student_id: str, new_student_id: str, new_name: str, new_class: str) -> bool:
    """Update a student's ID, name, and class."""
    old_student_id = old_student_id.strip()
    new_student_id = new_student_id.strip()
    
    try:
        with connection() as conn:
            # Disable foreign keys temporarily for this connection to allow manual cascade
            conn.execute("PRAGMA foreign_keys = OFF")
            
            cursor = conn.execute(
                "UPDATE students SET student_id = ?, name = ?, class_name = ? WHERE student_id = ?",
                (new_student_id, new_name.strip(), new_class.strip(), old_student_id),
            )
            if cursor.rowcount > 0 and old_student_id != new_student_id:
                conn.execute("UPDATE face_encodings SET student_id = ? WHERE student_id = ?", (new_student_id, old_student_id))
                conn.execute("UPDATE attendance SET student_id = ? WHERE student_id = ?", (new_student_id, old_student_id))
        return cursor.rowcount > 0
    except sqlite3.IntegrityError as exc:
        if "UNIQUE constraint failed" in str(exc) or "students.student_id" in str(exc):
            raise ValueError(f"Student ID '{new_student_id}' is already in use.")
        raise DatabaseError("Could not update student") from exc


def get_students_with_encoding_counts(
    class_name: str | None = None,
) -> list[tuple[str, str, str, int, str]]:
    """Return students with secure encoding counts and creation timestamps."""
    query = """
        SELECT s.student_id, s.name, s.class_name,
               SUM(CASE WHEN fe.encoding_format = 'npy' THEN 1 ELSE 0 END) AS encoding_count,
               s.created_at
        FROM students s
        LEFT JOIN face_encodings fe ON fe.student_id = s.student_id
    """
    params: tuple[str, ...] = ()
    if class_name is not None:
        query += " WHERE s.class_name = ?"
        params = (class_name.strip(),)
    query += " GROUP BY s.student_id, s.name, s.class_name, s.created_at ORDER BY s.student_id"
    with connection() as conn:
        return conn.execute(query, params).fetchall()


def get_classes() -> list[str]:
    with connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT class_name FROM students "
            "WHERE TRIM(class_name) <> '' ORDER BY class_name"
        ).fetchall()
    return [row[0] for row in rows]


def get_sessions(limit: int | None = None) -> list[str]:
    query = (
        "SELECT session_name FROM attendance GROUP BY session_name "
        "ORDER BY MAX(date || ' ' || time) DESC, session_name"
    )
    params: tuple[int, ...] = ()
    if limit is not None:
        query += " LIMIT ?"
        params = (limit,)
    with connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [row[0] for row in rows]


def get_recent_attendance(limit: int = 10) -> list[tuple[str, str, str, str, str, str, str]]:
    if limit <= 0:
        return []
    with connection() as conn:
        return conn.execute(
            """
            SELECT a.student_id, a.name, s.class_name, a.session_name,
                   a.date, a.time, a.status
            FROM attendance a
            JOIN students s ON a.student_id = s.student_id
            ORDER BY a.date DESC, a.time DESC, a.id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()


def get_attendance_filtered(
    target_date: str | None = None,
    session_name: str | None = None,
    class_name: str | None = None,
) -> list[tuple[str, str, str, str, str, str, str]]:
    """Return attendance using optional date, session, and class filters."""
    query = """
        SELECT a.student_id, a.name, s.class_name, a.session_name,
               a.date, a.time, a.status
        FROM attendance a
        JOIN students s ON a.student_id = s.student_id
        WHERE 1 = 1
    """
    params: list[str] = []
    if target_date is not None:
        query += " AND a.date = ?"
        params.append(target_date)
    if session_name is not None:
        query += " AND a.session_name = ?"
        params.append(session_name.strip())
    if class_name is not None:
        query += " AND s.class_name = ?"
        params.append(class_name.strip())
    query += " ORDER BY a.date DESC, a.session_name, a.time, a.student_id"
    with connection() as conn:
        return conn.execute(query, params).fetchall()


if __name__ == "__main__":
    init_db()
    print(f"Database initialized at {DB_PATH}")

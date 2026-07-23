from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

import database as db


class DatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = str(Path(self.temp_dir.name) / "test_attendance.db")
        db.init_db()

    def tearDown(self) -> None:
        db.DB_PATH = self.original_path
        self.temp_dir.cleanup()

    def test_student_and_secure_encoding_round_trip(self) -> None:
        self.assertTrue(db.add_student("S001", "Alice Smith", "CS101"))
        self.assertFalse(db.add_student("S001", "Another Person", "CS101"))

        encoding = np.linspace(0, 1, 128, dtype=np.float64)
        db.save_encoding("S001", encoding)
        encodings, ids, names = db.load_all_encodings()

        self.assertEqual(ids, ["S001"])
        self.assertEqual(names, {"S001": "Alice Smith"})
        np.testing.assert_allclose(encodings[0], encoding)

    def test_unknown_student_encoding_is_an_error(self) -> None:
        with self.assertRaises(db.StudentNotFoundError):
            db.save_encoding("UNKNOWN", np.zeros(128))

    def test_attendance_is_unique_per_session_not_per_day(self) -> None:
        db.add_student("S001", "Alice Smith", "CS101")
        self.assertTrue(
            db.mark_attendance(
                "S001", "Morning", attendance_date="2026-07-23", attendance_time="09:00:00"
            )
        )
        self.assertFalse(
            db.mark_attendance(
                "S001", "Morning", attendance_date="2026-07-23", attendance_time="09:01:00"
            )
        )
        self.assertTrue(
            db.mark_attendance(
                "S001", "Afternoon", attendance_date="2026-07-23", attendance_time="14:00:00"
            )
        )
        self.assertEqual(len(db.get_attendance_by_date("2026-07-23")), 2)

    def test_unknown_student_attendance_is_not_reported_as_duplicate(self) -> None:
        with self.assertRaises(db.StudentNotFoundError):
            db.mark_attendance("UNKNOWN", "Morning")

    def test_invalid_encoding_shape_is_rejected(self) -> None:
        db.add_student("S001", "Alice Smith")
        with self.assertRaises(ValueError):
            db.save_encoding("S001", np.zeros(127))

    def test_original_database_schema_is_migrated(self) -> None:
        # Recreate the database using the original project's schema.
        Path(db.DB_PATH).unlink(missing_ok=True)
        import sqlite3

        conn = sqlite3.connect(db.DB_PATH)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "CREATE TABLE students ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "student_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL, "
            "class_name TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(
            "CREATE TABLE face_encodings ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, "
            "encoding BLOB NOT NULL, "
            "FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE)"
        )
        conn.execute(
            "CREATE TABLE attendance ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, student_id TEXT NOT NULL, "
            "name TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL, "
            "status TEXT DEFAULT 'Present', "
            "FOREIGN KEY (student_id) REFERENCES students(student_id) ON DELETE CASCADE, "
            "UNIQUE(student_id, date))"
        )
        conn.execute(
            "INSERT INTO students (student_id, name, class_name) VALUES ('S001', 'Alice', 'CS101')"
        )
        conn.execute(
            "INSERT INTO face_encodings (student_id, encoding) VALUES ('S001', ?)",
            (b'legacy-pickle-placeholder',),
        )
        conn.execute(
            "INSERT INTO attendance (student_id, name, date, time) "
            "VALUES ('S001', 'Alice', '2026-07-23', '09:00:00')"
        )
        conn.commit()
        conn.close()

        db.init_db()
        rows = db.get_attendance_by_date('2026-07-23', 'default')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][3], 'default')

        with db.connection() as migrated:
            encoding_format = migrated.execute(
                "SELECT encoding_format FROM face_encodings"
            ).fetchone()[0]
        self.assertEqual(encoding_format, 'legacy_pickle')
        encodings, ids, names = db.load_all_encodings()
        self.assertEqual((encodings, ids, names), ([], [], {}))

    def test_web_dashboard_helpers_and_delete(self) -> None:
        db.add_student("S002", "Bob Jones", "CS102")
        db.add_student("S001", "Alice Smith", "CS101")
        db.save_encoding("S001", np.zeros(128))
        db.mark_attendance(
            "S001", "Morning", attendance_date="2026-07-23", attendance_time="09:00:00"
        )

        students = db.get_students_with_encoding_counts()
        self.assertEqual([row[0] for row in students], ["S001", "S002"])
        self.assertEqual(students[0][3], 1)
        self.assertEqual(students[1][3], 0)
        self.assertEqual(db.get_classes(), ["CS101", "CS102"])
        self.assertEqual(db.get_sessions(), ["Morning"])
        self.assertEqual(len(db.get_recent_attendance()), 1)
        self.assertEqual(
            len(db.get_attendance_filtered("2026-07-23", "Morning", "CS101")), 1
        )
        self.assertEqual(
            db.get_attendance_filtered("2026-07-23", "Morning", "CS102"), []
        )

        self.assertTrue(db.delete_student("S001"))
        self.assertFalse(db.delete_student("S001"))
        self.assertEqual(db.get_attendance_by_date("2026-07-23"), [])



if __name__ == "__main__":
    unittest.main()

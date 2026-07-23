from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

try:
    from app import app
    import database as db
except ModuleNotFoundError as exc:
    app = None
    db = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


@unittest.skipIf(app is None, f"Web dependencies are not installed: {IMPORT_ERROR}")
class WebAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = db.DB_PATH
        db.DB_PATH = str(Path(self.temp_dir.name) / "web_test.db")
        db.init_db()
        app.config.update(TESTING=True, SECRET_KEY="test-secret")
        self.client = app.test_client()

    def tearDown(self) -> None:
        db.DB_PATH = self.original_path
        self.temp_dir.cleanup()

    def csrf_token(self) -> str:
        self.client.get("/")
        with self.client.session_transaction() as flask_session:
            return flask_session["csrf_token"]

    def test_main_pages_render(self) -> None:
        for path in ["/", "/students", "/students/register", "/attendance", "/reports"]:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn(b"FaceTrack", response.data)

    def test_manual_attendance_and_csv_download(self) -> None:
        db.add_student("S001", "Alice Smith", "CS101")
        response = self.client.post(
            "/api/attendance/manual",
            json={"student_id": "S001", "session_name": "Morning"},
            headers={"X-CSRF-Token": self.csrf_token()},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["inserted"])

        report = self.client.get("/reports/export?scope=all&format=csv")
        self.assertEqual(report.status_code, 200)
        self.assertEqual(report.mimetype, "text/csv")
        self.assertIn(b"Alice Smith", report.data)

    def test_delete_requires_csrf_and_cascades(self) -> None:
        db.add_student("S001", "Alice Smith", "CS101")
        rejected = self.client.post("/students/S001/delete")
        self.assertEqual(rejected.status_code, 400)
        accepted = self.client.post(
            "/students/S001/delete",
            data={"csrf_token": self.csrf_token()},
        )
        self.assertEqual(accepted.status_code, 302)
        self.assertIsNone(db.get_student("S001"))


if __name__ == "__main__":
    unittest.main()

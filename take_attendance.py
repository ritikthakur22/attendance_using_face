"""Real-time recognition and session-based attendance marking."""

from __future__ import annotations

import argparse
import time
from collections import defaultdict

import cv2
import face_recognition
import numpy as np

import database as db


def positive_int(value: str) -> int:
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return number


def nonnegative_int(value: str) -> int:
    number = int(value)
    if number < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return number


def tolerance_value(value: str) -> float:
    number = float(value)
    if not 0.0 < number <= 1.0:
        raise argparse.ArgumentTypeError("must be greater than 0 and at most 1")
    return number


def resize_value(value: str) -> float:
    number = float(value)
    if not 0.0 < number <= 1.0:
        raise argparse.ArgumentTypeError("must be greater than 0 and at most 1")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run real-time face recognition attendance")
    parser.add_argument("--tolerance", type=tolerance_value, default=0.5, help="Lower is stricter")
    parser.add_argument("--process-every", type=positive_int, default=2, help="Process every Nth frame")
    parser.add_argument("--resize", type=resize_value, default=0.25, help="Processing scale from 0 to 1")
    parser.add_argument("--camera", type=nonnegative_int, default=0, help="Camera index")
    parser.add_argument("--session", default="default", help="Lesson or session name")
    parser.add_argument("--class", dest="class_name", help="Only recognize students in this class")
    parser.add_argument(
        "--confirm-frames",
        type=positive_int,
        default=3,
        help="Required recognized processed frames before attendance is marked",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    session_name = args.session.strip()
    if not session_name:
        print("Error: --session must not be blank.")
        return 2

    db.init_db()
    print("Loading known face encodings from database...")
    known_encodings, known_ids, id_to_name = db.load_all_encodings(args.class_name)
    if not known_encodings:
        scope = f" for class {args.class_name!r}" if args.class_name else ""
        print(f"No secure registered face encodings found{scope}. Run register_student.py first.")
        return 1

    registered_ids = set(known_ids)
    marked_today = {
        row[0] for row in db.get_attendance_by_date(session_name=session_name)
    }
    confirmation_counts: dict[str, int] = defaultdict(int)

    cap = cv2.VideoCapture(args.camera)
    if not cap.isOpened():
        print(f"Error: could not open webcam at camera index {args.camera}.")
        return 1

    print(
        f"Starting session {session_name!r}. "
        f"A face must match in {args.confirm_frames} processed frames. Press 'q' to quit."
    )

    frame_count = 0
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                print("Error: failed to grab a webcam frame.")
                return 1

            frame_count += 1
            draw_results: list[tuple[tuple[int, int, int, int], str, str | None, int]] = []

            if frame_count % args.process_every == 0:
                small_frame = cv2.resize(frame, (0, 0), fx=args.resize, fy=args.resize)
                rgb_small_frame = cv2.cvtColor(small_frame, cv2.COLOR_BGR2RGB)
                locations = face_recognition.face_locations(rgb_small_frame, model="hog")
                encodings = face_recognition.face_encodings(rgb_small_frame, locations)

                seen_this_frame: set[str] = set()
                for location, face_encoding in zip(locations, encodings):
                    name = "Unknown"
                    student_id = None
                    distance_display = 0

                    distances = face_recognition.face_distance(known_encodings, face_encoding)
                    if len(distances):
                        best_index = int(np.argmin(distances))
                        best_distance = float(distances[best_index])
                        distance_display = int(round(best_distance * 100))
                        if best_distance <= args.tolerance:
                            student_id = known_ids[best_index]
                            name = id_to_name[student_id]
                            seen_this_frame.add(student_id)

                    if student_id and student_id not in marked_today:
                        confirmation_counts[student_id] += 1
                        if confirmation_counts[student_id] >= args.confirm_frames:
                            if db.mark_attendance(student_id, session_name=session_name):
                                print(
                                    f"[ATTENDANCE] {name} ({student_id}) marked present "
                                    f"for {session_name} at {time.strftime('%H:%M:%S')}"
                                )
                            marked_today.add(student_id)

                    draw_results.append((location, name, student_id, distance_display))

                # Require consecutive processed-frame recognition.
                for student_id in list(confirmation_counts):
                    if student_id not in seen_this_frame and student_id not in marked_today:
                        confirmation_counts[student_id] = 0

            scale = 1.0 / args.resize
            for (top, right, bottom, left), name, student_id, distance in draw_results:
                top, right, bottom, left = [int(value * scale) for value in (top, right, bottom, left)]

                if student_id is None:
                    color = (0, 0, 255)
                    label = "Unknown"
                elif student_id in marked_today:
                    color = (0, 200, 0)
                    label = f"{name} (Present)"
                else:
                    color = (0, 165, 255)
                    progress = confirmation_counts[student_id]
                    label = f"{name} ({progress}/{args.confirm_frames})"

                cv2.rectangle(frame, (left, top), (right, bottom), color, 2)
                label_top = max(top, bottom - 28)
                cv2.rectangle(frame, (left, label_top), (right, bottom), color, cv2.FILLED)
                cv2.putText(
                    frame,
                    label,
                    (left + 6, bottom - 7),
                    cv2.FONT_HERSHEY_DUPLEX,
                    0.55,
                    (255, 255, 255),
                    1,
                )

            cv2.putText(
                frame,
                f"Session: {session_name} | Marked: {len(marked_today)}/{len(registered_ids)}",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (255, 255, 255),
                2,
            )
            cv2.putText(
                frame,
                "Press 'q' to quit",
                (10, frame.shape[0] - 15),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                (255, 255, 255),
                1,
            )
            cv2.imshow("Face Recognition Attendance", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
    except db.DatabaseError as exc:
        print(f"Database error: {exc}")
        return 1
    finally:
        cap.release()
        cv2.destroyAllWindows()

    print(f"Session ended. Total marked present: {len(marked_today)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

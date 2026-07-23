"""Register students and securely store face encodings."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import tempfile
from pathlib import Path

import cv2
import face_recognition

import database as db

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png"}


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


def safe_component(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip()).strip("._")
    return cleaned or fallback


def capture_from_webcam(
    student_id: str,
    name: str,
    num_images: int = 8,
    camera: int = 0,
    save_dir: str | None = None,
) -> str:
    """Capture frames containing exactly one detectable face."""
    if save_dir is None:
        person_dir = tempfile.mkdtemp(prefix="attendance_capture_")
    else:
        folder_name = f"{safe_component(student_id, 'student')}_{safe_component(name, 'name')}"
        person_dir = os.path.join(save_dir, folder_name)
        os.makedirs(person_dir, exist_ok=True)

    cap = cv2.VideoCapture(camera)
    if not cap.isOpened():
        shutil.rmtree(person_dir, ignore_errors=True)
        raise RuntimeError(f"Could not open webcam at camera index {camera}.")

    print("Press SPACE to capture a frame containing exactly one face; ESC stops early.")
    print(f"Capturing {num_images} images for {name} ({student_id})")

    count = 0
    try:
        while count < num_images:
            ret, frame = cap.read()
            if not ret:
                raise RuntimeError("Failed to read a frame from the webcam.")

            display = frame.copy()
            cv2.putText(
                display,
                f"Captured: {count}/{num_images} [SPACE=capture, ESC=quit]",
                (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.65,
                (0, 255, 0),
                2,
            )
            cv2.imshow("Register Student - " + name, display)

            key = cv2.waitKey(1) & 0xFF
            if key == 27:
                break
            if key != 32:
                continue

            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            locations = face_recognition.face_locations(rgb_frame, model="hog")
            if len(locations) != 1:
                print(f"  [skip] Expected exactly one face; found {len(locations)}.")
                continue

            img_path = os.path.join(person_dir, f"{count:02d}.jpg")
            if not cv2.imwrite(img_path, frame):
                raise RuntimeError(f"Could not save captured image: {img_path}")
            print(f"  [saved] {img_path}")
            count += 1
    finally:
        cap.release()
        cv2.destroyAllWindows()

    return person_dir


def collect_encodings(folder_path: str) -> list:
    """Return encodings from images that contain exactly one face."""
    folder = Path(folder_path).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"Image folder does not exist or is not a directory: {folder}")

    image_files = sorted(
        path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in VALID_EXTENSIONS
    )
    if not image_files:
        raise ValueError(f"No JPG, JPEG, or PNG images found in {folder}")

    encodings = []
    for img_path in image_files:
        try:
            image = face_recognition.load_image_file(str(img_path))
            locations = face_recognition.face_locations(image, model="hog")
            if len(locations) == 0:
                print(f"  [skip] No face found in {img_path.name}")
                continue
            if len(locations) > 1:
                print(f"  [skip] Multiple faces found in {img_path.name}")
                continue

            found = face_recognition.face_encodings(image, known_face_locations=locations)
            if len(found) != 1:
                print(f"  [skip] Could not create one encoding for {img_path.name}")
                continue
            encodings.append(found[0])
            print(f"  [ok] Encoded {img_path.name}")
        except (OSError, ValueError, RuntimeError) as exc:
            print(f"  [skip] Could not process {img_path.name}: {exc}")

    return encodings


def validate_existing_student(
    student_id: str,
    name: str,
    class_name: str,
    add_images: bool,
) -> bool:
    """Return True when this is an existing, verified student."""
    existing = db.get_student(student_id)
    if existing is None:
        return False

    _, stored_name, stored_class = existing
    if stored_name.casefold() != name.casefold() or stored_class.casefold() != class_name.casefold():
        raise ValueError(
            f"Student ID {student_id!r} belongs to {stored_name!r} "
            f"in class {stored_class!r}; refusing to attach different identity data."
        )
    if not add_images:
        raise ValueError(
            f"Student {student_id} already exists. Use --add-images only when intentionally "
            "adding more photos for the same student."
        )
    return True


def process_images_in_folder(
    folder_path: str,
    student_id: str,
    name: str,
    class_name: str,
    add_images: bool = False,
) -> int:
    student_id = student_id.strip()
    name = name.strip()
    class_name = class_name.strip()
    if not student_id or not name:
        raise ValueError("Student ID and name must not be empty.")

    existing = validate_existing_student(student_id, name, class_name, add_images)
    encodings = collect_encodings(folder_path)
    if not encodings:
        raise ValueError("No valid single-face images were found; nothing was saved.")

    if not existing:
        if not db.add_student(student_id, name, class_name):
            raise RuntimeError(f"Could not create student {student_id}; the ID now exists.")
        print(f"Added student record: {student_id} - {name}")

    try:
        count = db.save_encodings(student_id, encodings)
    except Exception:
        # Avoid an empty student record if saving unexpectedly fails during first registration.
        if not existing:
            with db.connection() as conn:
                conn.execute("DELETE FROM students WHERE student_id = ?", (student_id,))
        raise
    return count


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Register a student for face recognition attendance")
    parser.add_argument("--id", required=True, help="Unique student ID, for example S001")
    parser.add_argument("--name", required=True, help="Student full name")
    parser.add_argument("--class", dest="class_name", default="", help="Class or section name")

    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--webcam", action="store_true", help="Capture images from a webcam")
    source.add_argument("--folder", help="Folder containing images of this student")

    parser.add_argument("--num-images", type=positive_int, default=8, help="Webcam images to capture")
    parser.add_argument("--camera", type=nonnegative_int, default=0, help="Camera index")
    parser.add_argument(
        "--add-images",
        action="store_true",
        help="Explicitly add encodings to an existing student whose name and class match",
    )
    parser.add_argument(
        "--keep-images",
        action="store_true",
        help="Keep webcam captures under dataset/ (temporary by default)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    db.init_db()

    student_id = args.id.strip()
    name = args.name.strip()
    class_name = args.class_name.strip()
    if not student_id or not name:
        print("Error: --id and --name must not be blank.")
        return 2

    capture_dir: str | None = None
    temporary_capture = False
    try:
        # Fail before opening the webcam when an existing ID is being reused incorrectly.
        validate_existing_student(student_id, name, class_name, args.add_images)

        if args.webcam:
            capture_dir = capture_from_webcam(
                student_id,
                name,
                args.num_images,
                args.camera,
                save_dir="dataset" if args.keep_images else None,
            )
            temporary_capture = not args.keep_images
            folder = capture_dir
        else:
            folder = args.folder

        count = process_images_in_folder(
            folder,
            student_id,
            name,
            class_name,
            add_images=args.add_images,
        )
        print(f"\nDone. {count} secure face encoding(s) saved for {name} ({student_id}).")
        return 0
    except (ValueError, RuntimeError, db.DatabaseError) as exc:
        print(f"Error: {exc}")
        return 1
    finally:
        if temporary_capture and capture_dir:
            shutil.rmtree(capture_dir, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())

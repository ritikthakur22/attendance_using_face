# Contributing to FaceTrack

Thank you for improving FaceTrack. Keep changes focused, testable, and mindful
that face encodings and attendance records are sensitive data.

## Local development

1. Fork and clone the repository.
2. Create a branch from `main`.
3. Create and activate a Python 3.11 or 3.12 virtual environment.
4. Install dependencies with `python -m pip install -r requirements.txt`.
5. Run `python -m unittest discover -s tests -v`.
6. Make the change and add or update tests.
7. Run the complete test suite again before opening a pull request.

See [README.md](README.md) for platform-specific setup.

## Pull requests

- Explain the problem and the chosen solution.
- Keep unrelated formatting or refactoring out of the change.
- Update documentation when setup, behavior, routes, or storage changes.
- Include tests for database and API behavior where practical.
- Never commit `attendance.db`, face photographs, exported reports, secrets, or
  other personal data.

## Recognition changes

Changes to tolerance, confirmation, face detection, or matching can alter false
acceptance and false rejection rates. Document the tradeoff and test with
non-sensitive sample data before proposing new defaults.

## Security reports

Do not open a public issue for a vulnerability involving biometric-data
exposure or unauthorized attendance changes. Follow [SECURITY.md](SECURITY.md).

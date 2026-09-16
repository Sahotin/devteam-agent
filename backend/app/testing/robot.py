from __future__ import annotations

from pathlib import Path
import xml.etree.ElementTree as ET

from backend.app.domain.artifacts import StructuredTestSummary, TestCaseFailure


ROBOT_RESULT_DIRECTORY = Path(".devteam") / "test-results" / "robot"
ROBOT_OUTPUT_FILE = ROBOT_RESULT_DIRECTORY / "output.xml"
ROBOT_LOG_FILE = ROBOT_RESULT_DIRECTORY / "log.html"
ROBOT_REPORT_FILE = ROBOT_RESULT_DIRECTORY / "report.html"
MAX_ROBOT_OUTPUT_BYTES = 10 * 1024 * 1024
IGNORED_DIRECTORIES = frozenset(
    {".git", ".venv", "node_modules", ".devteam", "release", "tmp"}
)


class RobotResultError(ValueError):
    """Raised when Robot Framework output is missing, unsafe or malformed."""


class RobotFrameworkRunner:
    """Resolve and parse a bounded Robot Framework project test run."""

    @staticmethod
    def discover_target(root: Path) -> str | None:
        for directory_name in ("tests", "robot"):
            candidate = root / directory_name
            if candidate.is_dir() and RobotFrameworkRunner._contains_suite(candidate):
                return directory_name
        root_suites = [path for path in root.glob("*.robot") if path.is_file()]
        if root_suites:
            return "."
        return None

    @staticmethod
    def command(python: str, root: Path) -> list[str] | None:
        target = RobotFrameworkRunner.discover_target(root)
        if target is None:
            return None
        return [
            python,
            "-m",
            "robot",
            "--outputdir",
            ROBOT_RESULT_DIRECTORY.as_posix(),
            "--output",
            "output.xml",
            "--log",
            "log.html",
            "--report",
            "report.html",
            "--console",
            "quiet",
            target,
        ]

    @staticmethod
    def prepare(root: Path) -> None:
        output_directory = root / ROBOT_RESULT_DIRECTORY
        output_directory.mkdir(parents=True, exist_ok=True)
        for relative_path in (ROBOT_OUTPUT_FILE, ROBOT_LOG_FILE, ROBOT_REPORT_FILE):
            target = root / relative_path
            if target.is_file():
                target.unlink()

    @staticmethod
    def parse(root: Path) -> StructuredTestSummary:
        output_path = root / ROBOT_OUTPUT_FILE
        if not output_path.is_file():
            raise RobotResultError("Robot Framework did not produce output.xml")
        size = output_path.stat().st_size
        if size < 1:
            raise RobotResultError("Robot Framework output.xml is empty")
        if size > MAX_ROBOT_OUTPUT_BYTES:
            raise RobotResultError("Robot Framework output.xml exceeds the 10 MiB limit")
        payload = output_path.read_bytes()
        upper_payload = payload.upper()
        if b"<!DOCTYPE" in upper_payload or b"<!ENTITY" in upper_payload:
            raise RobotResultError("Robot Framework output.xml contains forbidden declarations")
        try:
            document = ET.fromstring(payload)
        except ET.ParseError as error:
            raise RobotResultError(
                f"Robot Framework output.xml is malformed: {error}"
            ) from error

        failures: list[TestCaseFailure] = []
        passed = failed = skipped = 0
        duration_seconds = 0.0
        for suite in document.iter("suite"):
            suite_name = suite.attrib.get("name", "")
            suite_source = RobotFrameworkRunner._relative_source(
                root, suite.attrib.get("source")
            )
            for test in suite.findall("test"):
                status_element = test.find("status")
                if status_element is None:
                    continue
                status = status_element.attrib.get("status", "").upper()
                duration_seconds += RobotFrameworkRunner._duration(status_element)
                if status == "PASS":
                    passed += 1
                elif status in {"SKIP", "NOT RUN", "NOT_RUN"}:
                    skipped += 1
                else:
                    failed += 1
                    failures.append(
                        TestCaseFailure(
                            name=test.attrib.get("name", "unnamed test"),
                            suite=suite_name or None,
                            source=suite_source,
                            line=RobotFrameworkRunner._line(test.attrib.get("line")),
                            message=(status_element.text or "Robot test failed")[:2000],
                        )
                    )

        total = passed + failed + skipped
        if total == 0:
            raise RobotResultError("Robot Framework output.xml contains no test cases")
        return StructuredTestSummary(
            framework="robotframework",
            total=total,
            passed=passed,
            failed=failed,
            skipped=skipped,
            duration_ms=round(duration_seconds * 1000),
            failures=failures[:50],
            output_path=ROBOT_OUTPUT_FILE.as_posix(),
            log_path=(
                ROBOT_LOG_FILE.as_posix()
                if (root / ROBOT_LOG_FILE).is_file()
                else None
            ),
            report_path=(
                ROBOT_REPORT_FILE.as_posix()
                if (root / ROBOT_REPORT_FILE).is_file()
                else None
            ),
        )

    @staticmethod
    def _contains_suite(root: Path) -> bool:
        return any(
            path.is_file()
            and path.suffix.casefold() == ".robot"
            and not set(path.relative_to(root).parts).intersection(IGNORED_DIRECTORIES)
            for path in root.rglob("*.robot")
        )

    @staticmethod
    def _relative_source(root: Path, source: str | None) -> str | None:
        if not source:
            return None
        try:
            return Path(source).resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            return None

    @staticmethod
    def _line(value: str | None) -> int | None:
        try:
            line = int(value or "")
        except ValueError:
            return None
        return line if line > 0 else None

    @staticmethod
    def _duration(status: ET.Element) -> float:
        try:
            return max(0.0, float(status.attrib.get("elapsed", "0")))
        except ValueError:
            return 0.0

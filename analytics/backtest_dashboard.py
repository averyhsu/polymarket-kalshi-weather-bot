"""Local dashboard helpers for saved backtest artifacts."""

from __future__ import annotations

import json
import threading
import webbrowser
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STATIC_DASHBOARD_DIR = PROJECT_ROOT / "dashboard"


def list_backtest_artifacts(results_dir: Path) -> List[Dict[str, Any]]:
    """List saved backtest JSON artifacts newest-first."""

    resolved_dir = results_dir.resolve()
    if not resolved_dir.exists():
        return []
    artifacts = []
    for path in resolved_dir.glob("*.json"):
        stat = path.stat()
        artifacts.append(
            {
                "name": path.name,
                "path": str(path.resolve()),
                "modified_at_epoch": stat.st_mtime,
                "size_bytes": stat.st_size,
            }
        )
    return sorted(artifacts, key=lambda item: (item["modified_at_epoch"], item["name"]), reverse=True)


def resolve_backtest_artifact(results_dir: Path, artifact_path: Optional[Path] = None) -> Path:
    """Resolve a requested artifact path or fall back to the latest saved run."""

    resolved_dir = results_dir.resolve()
    if artifact_path is None:
        artifacts = list_backtest_artifacts(resolved_dir)
        if not artifacts:
            raise FileNotFoundError(f"No saved backtest JSON artifacts found in {resolved_dir}")
        return Path(artifacts[0]["path"])

    candidate = artifact_path.expanduser().resolve()
    if candidate.suffix.lower() != ".json":
        raise ValueError("Dashboard artifacts must be JSON files")
    if not candidate.exists():
        raise FileNotFoundError(f"Backtest artifact not found: {candidate}")
    return candidate


def load_backtest_artifact(artifact_path: Path) -> Dict[str, Any]:
    """Load a saved backtest artifact package from disk."""

    with artifact_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if "summary" not in payload or "diagnostics" not in payload or "details" not in payload:
        raise ValueError(f"Artifact is not a saved backtest package: {artifact_path}")
    return payload


def build_dashboard_payload(package: Dict[str, Any], artifact_path: Path) -> Dict[str, Any]:
    """Build the payload served to the local dashboard app."""

    return {
        "artifact": {
            "path": str(artifact_path.resolve()),
            "name": artifact_path.name,
        },
        "run": package["run"],
        "summary": package["summary"],
        "diagnostics": package["diagnostics"],
        "details": package["details"],
        "comparison": package.get("comparison"),
    }


def _make_handler(
    *,
    static_dir: Path,
    results_dir: Path,
    default_artifact_path: Path,
):
    class DashboardRequestHandler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(static_dir), **kwargs)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            return

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/api/artifacts":
                self._send_json(
                    {
                        "results_dir": str(results_dir),
                        "artifacts": list_backtest_artifacts(results_dir),
                        "default_artifact_path": str(default_artifact_path),
                    }
                )
                return
            if parsed.path == "/api/artifact":
                query = parse_qs(parsed.query)
                raw_path = query.get("path", [None])[0]
                artifact_path = resolve_backtest_artifact(
                    results_dir,
                    None if raw_path is None else Path(raw_path),
                )
                payload = build_dashboard_payload(load_backtest_artifact(artifact_path), artifact_path)
                self._send_json(payload)
                return
            if parsed.path in {"", "/"}:
                self.path = "/index.html"
            return super().do_GET()

        def _send_json(self, payload: Dict[str, Any], *, status: int = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return DashboardRequestHandler


def create_backtest_dashboard_server(
    *,
    results_dir: Path,
    artifact_path: Optional[Path] = None,
    host: str = "127.0.0.1",
    port: int = 0,
) -> tuple[ThreadingHTTPServer, str]:
    """Create a local dashboard server for saved backtest artifacts."""

    resolved_results_dir = results_dir.resolve()
    default_artifact_path = resolve_backtest_artifact(resolved_results_dir, artifact_path)
    handler_cls = _make_handler(
        static_dir=STATIC_DASHBOARD_DIR.resolve(),
        results_dir=resolved_results_dir,
        default_artifact_path=default_artifact_path,
    )
    server = ThreadingHTTPServer((host, port), handler_cls)
    server.daemon_threads = True
    address, selected_port = server.server_address
    return server, f"http://{address}:{selected_port}/"


def launch_backtest_dashboard(
    *,
    results_dir: Path,
    artifact_path: Optional[Path] = None,
    host: str = "127.0.0.1",
    port: int = 0,
    open_browser: bool = True,
    blocking: bool = True,
) -> str:
    """Launch the local dashboard server and optionally open it in a browser."""

    server, url = create_backtest_dashboard_server(
        results_dir=results_dir,
        artifact_path=artifact_path,
        host=host,
        port=port,
    )
    if open_browser:
        webbrowser.open(url)
    if blocking:
        try:
            server.serve_forever()
        finally:
            server.server_close()
    else:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
    return url

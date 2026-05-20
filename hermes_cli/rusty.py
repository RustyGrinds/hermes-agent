"""Rusty Agent bootstrap status helpers.

Rusty Agent is the RGP soft-fork lane for Hermes Agent.  This module keeps the
first source-level proof inspect-only so the fork remains easy to sync upstream.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parent.parent
RUSTY_STATUS_SCHEMA_VERSION = "rgp.rusty.status.v1"
RUSTY_BASELINE_COMMIT = "41f1eddee"
RUSTY_BRANCH_PREFIX = "rgp/rusty-agent"
DEFAULT_AGENT_PORTAL_URL = "http://127.0.0.1:8788"
DEFAULT_SKILLNASIUM_URL = "http://127.0.0.1:8765"


def _env_url(name: str, default: str) -> str:
    value = os.environ.get(name, "").strip()
    return value.rstrip("/") if value else default


def _run_git(args: list[str], cwd: Path = PROJECT_ROOT) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _parse_ahead_behind(value: str | None) -> tuple[int | None, int | None]:
    if not value:
        return None, None
    parts = value.split()
    if len(parts) != 2:
        return None, None
    try:
        return int(parts[0]), int(parts[1])
    except ValueError:
        return None, None


def get_git_state(cwd: Path = PROJECT_ROOT) -> dict[str, Any]:
    """Return local git state without mutating the repo."""
    branch = _run_git(["branch", "--show-current"], cwd)
    head = _run_git(["rev-parse", "--short=9", "HEAD"], cwd)
    full_head = _run_git(["rev-parse", "HEAD"], cwd)
    upstream = _run_git(["rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"], cwd)
    compare_ref = upstream
    if not compare_ref and _run_git(["rev-parse", "--verify", "origin/main"], cwd):
        compare_ref = "origin/main"
    ahead, behind = _parse_ahead_behind(
        _run_git(["rev-list", "--left-right", "--count", f"HEAD...{compare_ref}"], cwd)
        if compare_ref
        else None
    )
    porcelain = _run_git(["status", "--porcelain"], cwd)

    return {
        "branch": branch or "(unknown)",
        "head": head or "(unknown)",
        "full_head": full_head or "",
        "upstream": upstream or "(none)",
        "compare_ref": compare_ref or "(none)",
        "ahead": ahead,
        "behind": behind,
        "clean": porcelain == "",
        "is_rusty_branch": bool(branch and branch.startswith(RUSTY_BRANCH_PREFIX)),
        "is_baseline": bool(full_head and full_head.startswith(RUSTY_BASELINE_COMMIT)),
    }


def _probe_json(url: str, timeout: float = 1.5) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read(256_000).decode("utf-8", errors="replace")
    except HTTPError as exc:
        return {"ok": False, "status": "http-error", "detail": str(exc.code)}
    except (OSError, TimeoutError, URLError) as exc:
        return {"ok": False, "status": "offline", "detail": str(exc)}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "status": "bad-json", "detail": "response was not JSON"}
    return {"ok": True, "status": "ok", "payload": payload}


def _portal_state(base_url: str) -> dict[str, Any]:
    result = _probe_json(f"{base_url}/api/status", timeout=5)
    payload = result.get("payload") if result.get("ok") else {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "url": base_url,
        "ok": bool(result.get("ok")),
        "status": payload.get("status") or result.get("status") or "unknown",
        "mode": payload.get("portal", {}).get("mode") if isinstance(payload.get("portal"), dict) else None,
        "detail": result.get("detail"),
    }


def collect_portal_package(portal_url: str | None = None) -> dict[str, Any]:
    portal_base = (portal_url or _env_url("RGP_AGENT_PORTAL_URL", DEFAULT_AGENT_PORTAL_URL)).rstrip("/")
    result = _probe_json(f"{portal_base}/api/portal/package", timeout=8)
    payload = result.get("payload") if result.get("ok") else None
    if isinstance(payload, dict):
        return payload
    return {
        "ok": False,
        "schema_version": "unknown",
        "portal": {"urls": [{"label": "Configured", "url": portal_base}]},
        "error": result.get("detail") or result.get("status") or "Portal package unavailable",
    }


def _skillnasium_state(base_url: str) -> dict[str, Any]:
    health = _probe_json(f"{base_url}/api/health")
    installed = _probe_json(f"{base_url}/api/codex/installed")
    installed_payload = installed.get("payload") if installed.get("ok") else {}
    if not isinstance(installed_payload, dict):
        installed_payload = {}
    skills = installed_payload.get("skills", [])
    if not skills:
        skills = installed_payload.get("installed", [])
    if not isinstance(skills, list):
        skills = []
    return {
        "url": base_url,
        "ok": bool(health.get("ok")),
        "status": "ok" if health.get("ok") else health.get("status", "offline"),
        "installed_count": len(skills) if installed.get("ok") else None,
        "detail": health.get("detail"),
    }


def _portal_skillnasium_state(portal_base_url: str, service_state: dict[str, Any]) -> dict[str, Any]:
    installed = _probe_json(f"{portal_base_url}/api/codex/installed", timeout=3)
    installed_payload = installed.get("payload") if installed.get("ok") else {}
    if not isinstance(installed_payload, dict):
        installed_payload = {}
    skills = installed_payload.get("installed", [])
    if not isinstance(skills, list):
        skills = []
    return {
        "url": f"{portal_base_url}/skillnasium/",
        "service_url": service_state.get("url"),
        "ok": bool(installed_payload.get("ok")),
        "status": "portal-embedded" if installed_payload.get("ok") else service_state.get("status", "offline"),
        "installed_count": len(skills) if installed_payload.get("ok") else service_state.get("installed_count"),
        "detail": (
            "Agent Portal embedded Skillnasium adapter is available."
            if installed_payload.get("ok")
            else service_state.get("detail") or installed.get("detail")
        ),
    }


def collect_rusty_status(
    portal_url: str | None = None,
    skillnasium_url: str | None = None,
    probe_portal: bool = True,
    cwd: Path = PROJECT_ROOT,
) -> dict[str, Any]:
    """Collect local Rusty readiness state without changing anything."""
    portal_base = (portal_url or _env_url("RGP_AGENT_PORTAL_URL", DEFAULT_AGENT_PORTAL_URL)).rstrip("/")
    skillnasium_base = (
        skillnasium_url or _env_url("RGP_SKILLNASIUM_URL", DEFAULT_SKILLNASIUM_URL)
    ).rstrip("/")
    git = get_git_state(cwd)
    portal = (
        _portal_state(portal_base)
        if probe_portal
        else {
            "url": portal_base,
            "ok": None,
            "status": "skipped",
            "mode": "provided by Agent Portal",
            "detail": "Portal probe skipped to avoid recursive status calls.",
        }
    )
    skillnasium = _skillnasium_state(skillnasium_base)
    if not skillnasium.get("ok"):
        embedded_skillnasium = _portal_skillnasium_state(portal_base, skillnasium)
        if embedded_skillnasium.get("ok"):
            skillnasium = embedded_skillnasium
    return {
        "schema_version": RUSTY_STATUS_SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "baseline": RUSTY_BASELINE_COMMIT,
        "git": git,
        "portal": portal,
        "skillnasium": skillnasium,
        "readiness": _readiness(git, portal, skillnasium),
        "guardrails": [
            "inspect-only CLI status",
            "Portal worker launch is operator-token and free/local model gated",
            "Portal Skillnasium install uses explicit Rusty/Codex targets",
            "Rusty changes should be ledgered before widening behavior",
        ],
    }


def _fmt_bool(value: bool) -> str:
    return "yes" if value else "no"


def _fmt_count(value: int | None) -> str:
    return str(value) if value is not None else "unknown"


def _readiness(git: dict[str, Any], portal: dict[str, Any], skillnasium: dict[str, Any]) -> dict[str, Any]:
    checks = {
        "rusty_branch": bool(git.get("is_rusty_branch")),
        "git_compare_available": bool(git.get("compare_ref") and git.get("compare_ref") != "(none)"),
        "portal_available_or_skipped": portal.get("ok") is not False,
        "skillnasium_available": bool(skillnasium.get("ok")),
    }
    return {
        "ready": all(checks.values()),
        "checks": checks,
    }


def render_rusty_status(report: dict[str, Any]) -> str:
    git = report.get("git", {})
    portal = report.get("portal", {})
    skillnasium = report.get("skillnasium", {})
    ahead = git.get("ahead")
    behind = git.get("behind")
    sync = "unknown"
    if ahead is not None and behind is not None:
        sync = f"ahead {ahead}, behind {behind}"

    compare_ref = git.get("compare_ref") or git.get("upstream") or "(none)"
    lines = [
        "Rusty Agent bootstrap",
        "----------------------",
        f"Branch:      {git.get('branch', '(unknown)')}",
        f"HEAD:        {git.get('head', '(unknown)')}",
        f"Baseline:    {report.get('baseline', RUSTY_BASELINE_COMMIT)}",
        f"Upstream:    {git.get('upstream', '(none)')}",
        f"Compare ref: {compare_ref}",
        f"Sync:        {sync}",
        f"Clean tree:  {_fmt_bool(bool(git.get('clean')))}",
        f"Rusty lane:  {_fmt_bool(bool(git.get('is_rusty_branch')))}",
        "",
        "Local control plane",
        f"Agent Portal: {portal.get('status', 'unknown')} ({portal.get('url', DEFAULT_AGENT_PORTAL_URL)})",
        f"Portal mode:  {portal.get('mode') or 'unknown'}",
        f"Skillnasium:  {skillnasium.get('status', 'unknown')} ({skillnasium.get('url', DEFAULT_SKILLNASIUM_URL)})",
        f"Skills seen:  {_fmt_count(skillnasium.get('installed_count'))}",
        "",
        "Guardrails",
    ]
    lines.extend(f"- {item}" for item in report.get("guardrails", []))
    lines.extend(
        [
            "",
            "Next safe step: keep Rusty changes small, inspectable, and ledgered until a hard fork is justified.",
        ]
    )
    return "\n".join(lines)


def _first_title(items: list[dict[str, Any]], fallback: str) -> str:
    if items:
        title = items[0].get("title")
        if title:
            return str(title)
    return fallback


def render_portal_package(package: dict[str, Any]) -> str:
    counts = package.get("counts", {})
    guardrails = package.get("guardrails", {})
    rusty = package.get("rusty", {})
    rusty_git = rusty.get("git", {}) if isinstance(rusty, dict) else {}
    rusty_readiness = rusty.get("readiness", {}) if isinstance(rusty, dict) else {}
    missions = package.get("missions", [])
    runbooks = package.get("runbooks", [])
    artifacts = package.get("artifacts", [])
    events = package.get("events", [])
    if not isinstance(missions, list):
        missions = []
    if not isinstance(runbooks, list):
        runbooks = []
    if not isinstance(artifacts, list):
        artifacts = []
    if not isinstance(events, list):
        events = []

    lines = [
        "RGP Agent Portal mission package",
        "--------------------------------",
        f"Schema:      {package.get('schema_version', 'unknown')}",
        f"Generated:   {package.get('generated_at', 'unknown')}",
        f"Mission:     {_first_title(missions, 'none')}",
        f"Rusty lane:  {rusty_git.get('branch', 'unknown')} @ {rusty_git.get('head', 'unknown')}",
        f"Readiness:   {'ready' if rusty_readiness.get('ready') else 'check'}",
        "",
        "Mission memory",
        f"Runbooks:    {counts.get('runbooks', len(runbooks))}",
        f"Tasks:       {counts.get('tasks', 'unknown')}",
        f"Artifacts:   {counts.get('artifacts', len(artifacts))}",
        f"Approvals:   {counts.get('approvals', 'unknown')}",
        "",
        "Guardrails",
        f"Worker launch: {guardrails.get('worker_launch', 'unknown')}",
        f"Skill install: {guardrails.get('skill_install', 'unknown')}",
        f"Mutation:      {guardrails.get('portal_mutation', 'unknown')}",
    ]
    if artifacts:
        lines.extend(["", "Recent artifacts"])
        for artifact in artifacts[:5]:
            lines.append(f"- {artifact.get('title', 'untitled')} [{artifact.get('kind', 'artifact')}]")
    if events:
        lines.extend(["", "Recent events"])
        for event in events[:5]:
            lines.append(f"- {event.get('title', 'event')}")
    return "\n".join(lines)


def show_rusty_status(args) -> None:
    portal_url = getattr(args, "portal_url", None)
    skillnasium_url = getattr(args, "skillnasium_url", None)
    report = collect_rusty_status(
        portal_url,
        skillnasium_url,
        probe_portal=not getattr(args, "no_portal_probe", False),
    )
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
        return
    print(render_rusty_status(report))


def show_rusty_portal(args) -> None:
    package = collect_portal_package(getattr(args, "portal_url", None))
    if getattr(args, "json", False):
        print(json.dumps(package, indent=2))
        return
    print(render_portal_package(package))


def run_rusty_command(args) -> None:
    command = getattr(args, "rusty_command", None)
    if command == "portal":
        show_rusty_portal(args)
        return
    show_rusty_status(args)

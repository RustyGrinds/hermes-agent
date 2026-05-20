import json
from types import SimpleNamespace
from pathlib import Path

from hermes_cli import rusty


def test_collect_rusty_status_uses_local_services(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rusty,
        "get_git_state",
        lambda cwd=Path("."): {
            "branch": "rgp/rusty-agent-bootstrap",
            "head": "41f1eddee",
            "full_head": "41f1eddee123",
            "upstream": "origin/main",
            "compare_ref": "origin/main",
            "ahead": 0,
            "behind": 0,
            "clean": True,
            "is_rusty_branch": True,
            "is_baseline": True,
        },
    )

    def fake_probe(url, timeout=1.5):
        if url.endswith("/api/status"):
            return {
                "ok": True,
                "status": "ok",
                "payload": {"status": "ok", "portal": {"mode": "guarded mission control"}},
            }
        if url.endswith("/api/health"):
            return {"ok": True, "status": "ok", "payload": {"ok": True}}
        if url.endswith("/api/codex/installed"):
            return {"ok": True, "status": "ok", "payload": {"installed": [{"name": "one"}]}}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(rusty, "_probe_json", fake_probe)

    report = rusty.collect_rusty_status(
        portal_url="http://portal.local/",
        skillnasium_url="http://skills.local/",
        cwd=tmp_path,
    )

    assert report["git"]["is_rusty_branch"] is True
    assert report["schema_version"] == rusty.RUSTY_STATUS_SCHEMA_VERSION
    assert report["readiness"]["ready"] is True
    assert report["portal"]["mode"] == "guarded mission control"
    assert report["skillnasium"]["installed_count"] == 1
    assert "Portal worker launch is operator-token and free/local model gated" in report["guardrails"]


def test_collect_rusty_status_can_skip_portal_probe(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rusty,
        "get_git_state",
        lambda cwd=Path("."): {
            "branch": "rgp/rusty-agent-bootstrap",
            "head": "41f1eddee",
            "full_head": "41f1eddee123",
            "upstream": "origin/main",
            "compare_ref": "origin/main",
            "ahead": 0,
            "behind": 0,
            "clean": True,
            "is_rusty_branch": True,
            "is_baseline": True,
        },
    )

    probed = []

    def fake_probe(url, timeout=1.5):
        probed.append(url)
        if url.endswith("/api/health"):
            return {"ok": True, "status": "ok", "payload": {"ok": True}}
        if url.endswith("/api/codex/installed"):
            return {"ok": True, "status": "ok", "payload": {"installed": []}}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(rusty, "_probe_json", fake_probe)

    report = rusty.collect_rusty_status(
        portal_url="http://portal.local",
        skillnasium_url="http://skills.local",
        probe_portal=False,
        cwd=tmp_path,
    )

    assert report["portal"]["status"] == "skipped"
    assert report["readiness"]["checks"]["portal_available_or_skipped"] is True
    assert all("/api/status" not in url for url in probed)


def test_collect_rusty_status_accepts_portal_embedded_skillnasium(monkeypatch, tmp_path):
    monkeypatch.setattr(
        rusty,
        "get_git_state",
        lambda cwd=Path("."): {
            "branch": "rgp/rusty-agent-bootstrap",
            "head": "41f1eddee",
            "full_head": "41f1eddee123",
            "upstream": "origin/main",
            "compare_ref": "origin/main",
            "ahead": 0,
            "behind": 0,
            "clean": True,
            "is_rusty_branch": True,
            "is_baseline": True,
        },
    )

    def fake_probe(url, timeout=1.5):
        if url == "http://skills.local/api/health":
            return {"ok": False, "status": "offline", "detail": "offline"}
        if url == "http://skills.local/api/codex/installed":
            return {"ok": False, "status": "offline", "detail": "offline"}
        if url == "http://portal.local/api/codex/installed":
            return {"ok": True, "status": "ok", "payload": {"ok": True, "installed": [{"name": "one"}]}}
        raise AssertionError(f"unexpected URL: {url}")

    monkeypatch.setattr(rusty, "_probe_json", fake_probe)

    report = rusty.collect_rusty_status(
        portal_url="http://portal.local",
        skillnasium_url="http://skills.local",
        probe_portal=False,
        cwd=tmp_path,
    )

    assert report["skillnasium"]["status"] == "portal-embedded"
    assert report["skillnasium"]["installed_count"] == 1
    assert report["readiness"]["ready"] is True


def test_render_rusty_status_is_inspect_only():
    output = rusty.render_rusty_status(
        {
            "baseline": "41f1eddee",
            "git": {
                "branch": "rgp/rusty-agent-bootstrap",
                "head": "41f1eddee",
                "upstream": "origin/main",
                "compare_ref": "origin/main",
                "ahead": 0,
                "behind": 0,
                "clean": True,
                "is_rusty_branch": True,
            },
            "portal": {
                "status": "ok",
                "url": "http://127.0.0.1:8788",
                "mode": "guarded mission control",
            },
            "skillnasium": {
                "status": "ok",
                "url": "http://127.0.0.1:8765",
                "installed_count": 1,
            },
            "readiness": {"ready": True, "checks": {}},
            "guardrails": ["inspect-only CLI status"],
        }
    )

    assert "Rusty Agent bootstrap" in output
    assert "Branch:      rgp/rusty-agent-bootstrap" in output
    assert "Agent Portal: ok (http://127.0.0.1:8788)" in output
    assert "- inspect-only CLI status" in output
    assert "Next safe step" in output


def test_show_rusty_status_can_emit_json(monkeypatch, capsys):
    monkeypatch.setattr(
        rusty,
        "collect_rusty_status",
        lambda portal_url=None, skillnasium_url=None, probe_portal=True: {
            "schema_version": rusty.RUSTY_STATUS_SCHEMA_VERSION,
            "readiness": {"ready": True, "checks": {}},
        },
    )

    rusty.show_rusty_status(
        SimpleNamespace(
            portal_url=None,
            skillnasium_url=None,
            no_portal_probe=True,
            json=True,
        )
    )

    output = json.loads(capsys.readouterr().out)
    assert output["schema_version"] == rusty.RUSTY_STATUS_SCHEMA_VERSION
    assert output["readiness"]["ready"] is True


def test_collect_portal_package_reads_agent_portal(monkeypatch):
    def fake_probe(url, timeout=1.5):
        assert url == "http://portal.local/api/portal/package"
        return {
            "ok": True,
            "status": "ok",
            "payload": {
                "ok": True,
                "schema_version": "rgp.agent.portal.package.v1",
                "counts": {"runbooks": 1, "artifacts": 2},
            },
        }

    monkeypatch.setattr(rusty, "_probe_json", fake_probe)

    package = rusty.collect_portal_package("http://portal.local")

    assert package["schema_version"] == "rgp.agent.portal.package.v1"
    assert package["counts"]["artifacts"] == 2


def test_render_portal_package_summarizes_mission_memory():
    output = rusty.render_portal_package(
        {
            "schema_version": "rgp.agent.portal.package.v1",
            "generated_at": "2026-05-18T20:00:00+00:00",
            "missions": [{"title": "Rusty Agent and Agent Portal foundation"}],
            "rusty": {
                "git": {"branch": "rgp/rusty-agent-bootstrap", "head": "41f1eddee"},
                "readiness": {"ready": True},
            },
            "counts": {"runbooks": 1, "tasks": 3, "artifacts": 6, "approvals": 2},
            "guardrails": {
                "worker_launch": "disabled",
                "skill_install": "view-only",
                "portal_mutation": "operator-token guarded",
            },
            "artifacts": [{"title": "Mission artifacts UI proof", "kind": "screenshot"}],
            "events": [{"title": "Mission artifacts added"}],
        }
    )

    assert "RGP Agent Portal mission package" in output
    assert "Mission:     Rusty Agent and Agent Portal foundation" in output
    assert "Artifacts:   6" in output
    assert "Worker launch: disabled" in output

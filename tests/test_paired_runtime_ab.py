from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from benchmarks import paired_runtime_ab as ab


def _config():
    return {"commits": {"old": "a" * 40, "new": "b" * 40},
            "backend": "deepseek", "model": "deepseek-flash",
            "max_steps": 60, "wall_seconds": 1800,
            "instances": [f"repo__issue-{n}" for n in range(8)]}


def test_protocol_rejects_unpaired_or_unpinned_runs(tmp_path):
    config = _config()
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    assert ab.load_protocol(path)["instances"] == config["instances"]
    config["commits"]["new"] = None
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="new commit"):
        ab.load_protocol(path)
    config = _config()
    config["instances"].append(config["instances"][0])
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError, match="unique"):
        ab.load_protocol(path)


def test_summary_keeps_runtime_candidate_separate_from_external_resolution(tmp_path):
    config = _config()
    first = config["instances"][0]
    for arm, status, ftp, ptp in (("old", "completed", "FAIL", "PASS"),
                                   ("new", "candidate", "PASS", "PASS")):
        directory = tmp_path / arm / first
        directory.mkdir(parents=True)
        (directory / "result.json").write_text(json.dumps({
            "agent_status": status, "ftp_before": "FAIL(符合预期)",
            "ftp_before_counts": {"failed": 1, "errors": 0},
            "ftp_after": ftp, "ptp_after": ptp, "duration_seconds": 12.5,
            "runtime_duration_seconds": 8.5,
            "input_tokens": 100, "output_tokens": 50,
            "replay_total_ms": 200 if arm == "new" else None,
        }), encoding="utf-8")
    result = ab.summarize(config, tmp_path)
    pair = result["pairs"][0]
    assert pair["old"]["false_success"] is True
    assert pair["new"]["runtime_status"] == "candidate"
    assert pair["new"]["external_resolved"] is True
    assert result["summary"]["arms"]["new"]["resolved"] == 1
    assert result["summary"]["arms"]["new"]["resolved_known"] == 1
    assert result["summary"]["arms"]["new"]["replay_total_ms_total"] is None
    assert result["pairs"][1]["old"]["external_resolved"] is None
    assert "未验证" in ab.render_markdown(result, config)


def test_runner_pairs_same_instance_with_sealed_probe(tmp_path, monkeypatch):
    config = _config()
    monkeypatch.setattr(ab, "validate", lambda *args: {"config": "fixed",
                                                       "dataset_sha256": "0" * 64})
    monkeypatch.setattr(ab, "_runtime_worktree",
                        lambda root, arm, commit: root / "_runtime" / arm)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        instance = command[command.index("--instance") + 1]
        result_root = Path(command[command.index("--result-dir") + 1])
        result_file = result_root / instance / "result.json"
        result_file.parent.mkdir(parents=True, exist_ok=True)
        result_file.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(ab.subprocess, "run", fake_run)
    ab.run(tmp_path / "config.json", config, tmp_path / "out", allow_unisolated=True)
    assert len(calls) == 16
    for pair in zip(calls[::2], calls[1::2]):
        assert pair[0][pair[0].index("--instance") + 1] == \
            pair[1][pair[1].index("--instance") + 1]
        for command in pair:
            assert "--sealed-ab" in command and "--no-hints" in command
            assert command[command.index("--max-steps") + 1] == "60"
            assert command[command.index("--wall-seconds") + 1] == "1800"


def test_resume_rejects_partial_acceptance_material(tmp_path, monkeypatch):
    config = _config()
    monkeypatch.setattr(ab, "validate", lambda *args: {"dataset_sha256": "0" * 64})
    output = tmp_path / "out"
    partial = output / "old" / config["instances"][0] / "_eval"
    partial.mkdir(parents=True)
    with pytest.raises(ValueError, match="partial acceptance artifacts"):
        ab.run(tmp_path / "config.json", config, output, resume=True,
               allow_unisolated=True)


def test_formal_run_fails_closed_without_filesystem_isolation(tmp_path):
    with pytest.raises(ValueError, match="OS-enforced"):
        ab.run(tmp_path / "config.json", _config(), tmp_path / "out")


def test_validate_checks_actual_imported_runtime_for_both_arms(tmp_path, monkeypatch):
    config = _config()
    monkeypatch.setattr(ab, "_cached_instances", lambda: {
        instance: {"FAIL_TO_PASS": ["test_a.py::test_a"],
                   "PASS_TO_PASS": ["test_b.py::test_b"]}
        for instance in config["instances"]})
    monkeypatch.setattr(ab, "_runtime_worktree",
                        lambda root, arm, commit: root / "_runtime" / arm)
    seen = []

    def fake_run(command, **kwargs):
        if command[:2] == ["git", "cat-file"]:
            return subprocess.CompletedProcess(command, 0)
        if command[:2] == ["git", "show"]:
            relative = command[2].split(":", 1)[1]
            return subprocess.CompletedProcess(command, 0, (ab.ROOT / relative).read_bytes())
        if "freeze" in command:
            return subprocess.CompletedProcess(command, 0, b"pytest==8.4\n")
        arm = "old" if "old" in kwargs["env"]["ZMAI_RUNTIME_SRC"] else "new"
        seen.append(arm)
        return subprocess.CompletedProcess(command, 0, json.dumps({
            "source": kwargs["env"]["ZMAI_RUNTIME_SRC"],
            "commit": config["commits"][arm],
            "model": config["model"],
        }))

    monkeypatch.setattr(ab.subprocess, "run", fake_run)
    manifest = ab.validate(tmp_path / "config.json", config, tmp_path / "out")
    assert seen == ["old", "new"]
    assert manifest["runtime_identities"]["new"]["commit"] == config["commits"]["new"]

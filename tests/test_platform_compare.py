"""Tests for experiments/platform_compare.py (synthetic circuits only)."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "experiments" / "platform_compare.py"


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


helpers = _load("test_mqt_pass_order_helpers_2h",
                ROOT / "tests" / "test_mqt_pass_order.py")
source_db = helpers.source_db  # pytest fixture


def compare(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, env=helpers.env(),
                          check=False)


def test_identical_runs_and_refusals(tmp_path, source_db):
    first = tmp_path / "first"
    assert helpers.call("select", "--source-db", str(source_db), "--out-dir",
                        str(first), "--count", "3").returncode == 0
    dirs = {}
    for name, pass_set in (("a", "abcfg"), ("b", "abcfg"),
                           ("c", "abc")):
        target = tmp_path / name
        assert helpers.call("reuse", "--from-dir", str(first),
                            "--out-dir", str(target)).returncode == 0
        run = helpers.call("run", "--out-dir", str(target), "--pass-set", pass_set)
        assert run.returncode == 0, run.stderr
        dirs[name] = target

    before = {p.name: p.read_bytes() for p in dirs["a"].iterdir()}
    result = compare("--a", str(dirs["a"]), "--b", str(dirs["b"]), "--json")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert data["summary"]["runs"] == 360
    assert data["summary"]["same_final_metrics"] == 360
    assert data["summary"]["identical_at_every_step"] == 360
    assert data["first_differing_pass"] == {}
    assert {p.name: p.read_bytes() for p in dirs["a"].iterdir()} == before

    text = compare("--a", str(dirs["a"]), "--b", str(dirs["b"]), "--metrics-only")
    assert "Same final gates/2q/depth: 360" in text.stdout

    refused = compare("--a", str(dirs["a"]), "--b", str(dirs["c"]))
    assert refused.returncode == 2 and "pass sets differ" in refused.stderr
    missing = compare("--a", str(dirs["a"]), "--b", str(tmp_path / "none"))
    assert missing.returncode == 2

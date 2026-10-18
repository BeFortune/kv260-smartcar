"""Offline collectors/analyzer smoke with synthetic reports; not board validation."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import numpy as np


def test_collect_and_analyze_complete_synthetic_reports(monkeypatch, tmp_path):
    here = Path(__file__).resolve().parent
    def module(name):
        spec = importlib.util.spec_from_file_location(name, here / (name + ".py"))
        value = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(value)
        return value
    analyzer, collector = module("analyze_board"), module("collect_board_evidence")
    run, program = tmp_path / "run", tmp_path / "program"
    run.mkdir()
    program.mkdir()
    dump = lambda path, value: path.write_text(json.dumps(value), encoding="utf-8")
    clock = {"ctrl": 4, "pl_raw": [1] * 4, "pll_raw": {"IOPLL": 2}, "cpu_raw": {"ACPU_CTRL": 3}, "fclk0_mhz": 100}
    dump(program / "report.json", {"status": "已验证", "hardware_programmed": True, "kernel_starts": 0, "after_snapshot": clock})
    dump(run / "report.json", {"status": "已验证"})
    output_names = ("speed_norm", "speed_raw", "region_logits", "region_probs")
    for mode in ("golden4", "split_ort4", "split_pl4"):
        directory = run / mode
        references = directory / "references"
        references.mkdir(parents=True)
        samples = [{"run_total_ms": 10., "offline_e2e_ms": 12., "ps_remaining_stage_ms": 3.,
                    "decode_preprocess_ms": 2., "cumulative_cut_errors_atol_1e_3_rtol_0": {n: 0. for n in ("camera_0", "camera_1", "cross_0", "cross_1")}}
                   for _ in range(117)]
        stats = lambda v: {"n": 117, "mean": v, "p50": v, "p95": v, "p99": v, "max": v}
        perf = {"status": "已验证", "frames": 117, "warmup": 3, "threads": 4,
                "spinning": None if mode == "golden4" else False, "raw_samples": samples,
                "run_total_ms": stats(10.), "offline_e2e_ms": stats(12.), "ps_remaining_stage_ms": stats(3.),
                "decode_preprocess_ms": stats(2.), "completion_interval_ms": stats(13.),
                "sustained_offline_outputs_hz": 1., "run_total_only_hz": 100., "test_duration_s": 117.,
                "initialization_ms_excluded": 20., "stage_ms": {},
                **{n: stats(1.) for n in ("ffn_prepare_ms", "ffn_hardware_wait_ms", "ffn_readback_ms", "ffn_total_ms")}}
        report = {"status": "已验证", "inputs": {"digest": "same"}, "phases": {"performance": perf, "layers": {}},
                  "kernel_starts_observed": 517, "expected_kernel_starts": 517, "final_clock_snapshot": clock}
        dump(directory / "report.json", report)
        for index in range(117):
            np.savez(references / f"frame_{index:03d}.npz", **{"out_" + n: np.zeros((1, 2 if n.startswith("speed") else 15), np.float32) for n in output_names})
    analysis = tmp_path / "analysis.json"
    monkeypatch.setattr(sys, "argv", ["analyze_board.py", "--run", str(run), "--program", str(program), "--output", str(analysis)])
    analyzer.main()
    assert json.loads(analysis.read_text())["four_output_max_abs_error"]["split_pl4"]["speed_raw"] == 0
    archive = tmp_path / "collected.zip"
    monkeypatch.setattr(sys, "argv", ["collect_board_evidence.py", "--run", str(run), "--program", str(program), "--output", str(archive)])
    collector.main()
    assert archive.is_file()

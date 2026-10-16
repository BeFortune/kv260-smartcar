"""Create a declared reduced cosim testbench without changing kernel or full tests."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HLS = ROOT / "hls"


def main():
    original = HLS / "testbench.cpp"
    original_bytes = original.read_bytes()
    content = original_bytes.decode("utf-8")
    changes = [
        ("const unsigned int small_counts[] = {1, 2, 3, 4, 5, 7, 8, 9};", "const unsigned int small_counts[] = {1, 4};"),
        ('        run_case("near-maximum", 659, x, first);\n', ""),
        ('run_case("camera-A", 660, x, first);', 'run_case("camera-A/reduced-cosim", 4, x, first);'),
        ('run_case("camera-B/reload", 660, x, random_weights(22));', 'run_case("camera-B/reload/reduced-cosim", 4, x, random_weights(22));'),
        ('std::cout << "All FFN reference tests passed.\\n";', 'std::cout << "Reduced-scope FFN RTL cosim tests passed (30 valid tokens; no 659/660-token RTL case).\\n";'),
    ]
    for old, new in changes:
        assert content.count(old) == 1, old
        content = content.replace(old, new, 1)
    reduced = HLS / "testbench_cosim_small.cpp"
    with reduced.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)
    config_original = HLS / "build/hls_config.cfg"
    config_bytes = config_original.read_bytes()
    config = config_bytes.decode("utf-8")
    assert config.count("../testbench.cpp") == 2
    config = config.replace("../testbench.cpp", "../testbench_cosim_small.cpp")
    config = "# Reduced-scope cosim only: 30 valid tokens, no 659/660-token RTL case.\n" + config
    config_reduced = HLS / "build/hls_config_cosim_small.cfg"
    with config_reduced.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(config)
    assert original.read_bytes() == original_bytes
    assert config_original.read_bytes() == config_bytes
    evidence = {
        "scope": "reduced",
        "declared_before_execution": True,
        "valid_token_total": 30,
        "valid_token_counts_by_case": [1, 4, 4, 4, 4, 4, 1, 4, 4],
        "invalid_counts": [0, 661, 4294967295],
        "not_covered_in_RTL": ["659-token invocation", "660-token invocation"],
        "preserved_checks": ["four distinct weight sets", "weight reload and revisit", "double reference all active elements", "tail elements of each reduced case", "output guards", "inactive output tokens", "zero", "bias/exact-erf", "invalid count no output changes"],
        "kernel_modified": False,
        "original_testbench_modified": False,
        "original_config_modified": False,
        "files": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in (original, reduced, config_original, config_reduced, HLS / "ffn_accel.cpp")],
        "status": "prepared_not_executed",
    }
    with (ROOT / "evidence/hls/cosim_scope.json").open("x", encoding="utf-8") as stream:
        json.dump(evidence, stream, indent=2)
        stream.write("\n")
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()

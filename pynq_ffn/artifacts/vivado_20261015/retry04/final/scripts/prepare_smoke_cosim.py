"""Prepare one complete 1-token RTL smoke transaction, explicitly reduced scope."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HLS = ROOT / "hls"


def main():
    original = HLS / "testbench.cpp"
    text = original.read_text(encoding="utf-8")
    beginning, remainder = text.split("int main() {", 1)
    assert beginning and remainder
    main_body = '''int main() {
    try {
        std::vector<float> x(ffn_config::max_tokens * D);
        std::mt19937 rng(20261014);
        fill_random(x, rng, 1.0f);
        const Weights weights = random_weights(11);
        run_case("RTL-smoke/reduced-scope", 1, x, weights);
        std::cout << "One-token reduced-scope RTL smoke test passed.\\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\\n';
        return 1;
    }
}
'''
    reduced = HLS / "testbench_cosim_smoke.cpp"
    with reduced.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(beginning + main_body)
    original_config = HLS / "build/hls_config.cfg"
    config = original_config.read_text(encoding="utf-8")
    assert config.count("../testbench.cpp") == 2
    config = "# One-token smoke RTL cosim only; full token/boundary/reload validation NOT covered.\n" + config.replace("../testbench.cpp", "../testbench_cosim_smoke.cpp")
    destination = HLS / "build/hls_config_cosim_smoke.cfg"
    with destination.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(config)
    report = {"scope": "one_token_smoke", "valid_transactions": 1, "valid_token_total": 1,
              "declared_before_execution": True,
              "checks": ["128 active outputs versus independent double reference", "finite values", "atol=2e-5 plus rtol=2e-5", "output guards", "all inactive outputs untouched"],
              "not_covered_in_RTL": ["4 tokens", "659/660 tokens", "four weight sets/reload/revisit", "zero", "bias-only", "invalid token counts"],
              "prior_30_token_attempt": {"exit_code": 1, "result": "actively stopped after first RTL transaction, no final C postcheck"},
              "kernel_modified": False,
              "original_testbench_modified": False,
              "files": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in (original, reduced, original_config, destination, HLS / "ffn_accel.cpp")],
              "status": "prepared_not_executed"}
    with (ROOT / "evidence/hls/cosim_smoke_scope.json").open("x", encoding="utf-8") as stream:
        json.dump(report, stream, indent=2)
        stream.write("\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()

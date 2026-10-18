"""Tile-eight boundary/reload bitwise checks using the existing C ABI validator."""
import importlib.util
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


def main():
    spec = importlib.util.spec_from_file_location("validation", ROOT / "tools/validate_cpp_parallel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    history = ROOT / "artifacts/ffn_opt_20261017"
    baselines = {name: module.HostFFN(history / sub / "ffn_host.dll")
                 for name, sub in (("serial", "baseline_host"), ("r03", "candidate_r03_host"))}
    candidate = module.HostFFN(HERE / "host/ffn_host.dll")
    rng = np.random.default_rng(20261018)

    def weights():
        return {key: rng.uniform(-0.2, 0.2, shape).astype(np.float32)
                for key, shape in (("w1", (128, 256)), ("b1", (256,)),
                                   ("w2", (256, 128)), ("b2", (128,)))}

    first, second = weights(), weights()
    results = []
    for index, count in enumerate((7, 8, 9, 15, 16, 17, 659, 660, 9, 7, 1, 17, 8)):
        value = rng.uniform(-1, 1, (count, 128)).astype(np.float32)
        w = first if index % 2 == 0 else second
        actual = candidate.forward("tile8", value, w)
        for name, baseline in baselines.items():
            result = module.compare(baseline.forward("tile8", value, w), actual)
            if not result["pass"]:
                raise AssertionError("Tile-eight mismatch at " + str(count))
            result.update(baseline=name, tokens=count, sequence_index=index,
                          weights="A" if index % 2 == 0 else "B")
            results.append(result)
    with (HERE / "tile8_bitwise.json").open("x", encoding="utf-8") as stream:
        json.dump({"status": "pass", "hardware_execution": False, "cases": results}, stream, indent=2)
    print("PASS 13 sequential tile-eight calls against each of serial and r03; zero bit mismatches")


if __name__ == "__main__":
    main()

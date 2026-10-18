"""Host-only Windows page guards: reject any access beyond the active token tail."""
import ctypes
import importlib.util
import json
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
PAGE = 4096
KERNEL = ctypes.WinDLL("kernel32", use_last_error=True)
KERNEL.VirtualAlloc.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32]
KERNEL.VirtualAlloc.restype = ctypes.c_void_p
KERNEL.VirtualProtect.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32,
                                  ctypes.POINTER(ctypes.c_uint32)]
KERNEL.VirtualProtect.restype = ctypes.c_int
KERNEL.VirtualFree.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32]
KERNEL.VirtualFree.restype = ctypes.c_int
FLOAT_PTR = ctypes.POINTER(ctypes.c_float)


class Guarded:
    def __init__(self, values):
        values = np.ascontiguousarray(values, dtype=np.float32)
        size = values.nbytes
        pages = (size + PAGE - 1) // PAGE
        self.base = KERNEL.VirtualAlloc(None, (pages + 2) * PAGE, 0x3000, 0x04)
        if not self.base:
            raise ctypes.WinError(ctypes.get_last_error())
        old = ctypes.c_uint32()
        for address in (self.base, self.base + (pages + 1) * PAGE):
            if not KERNEL.VirtualProtect(address, PAGE, 0x01, ctypes.byref(old)):
                raise ctypes.WinError(ctypes.get_last_error())
        self.address = self.base + (pages + 1) * PAGE - size
        self.ptr = ctypes.cast(self.address, FLOAT_PTR)
        self.array = np.ctypeslib.as_array(self.ptr, shape=(values.size,))
        self.array[:] = values.ravel()

    def close(self):
        if not KERNEL.VirtualFree(self.base, 0, 0x8000):
            raise ctypes.WinError(ctypes.get_last_error())


def main():
    spec = importlib.util.spec_from_file_location("validation", ROOT / "tools/validate_cpp_parallel.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    baseline = module.HostFFN(ROOT / "artifacts/ffn_opt_20261017/candidate_r03_host/ffn_host.dll")
    candidate = module.HostFFN(HERE / "host/ffn_host.dll")
    rng = np.random.default_rng(20261018)
    weights = {key: rng.uniform(-0.2, 0.2, shape).astype(np.float32)
               for key, shape in (("w1", (128, 256)), ("b1", (256,)),
                                  ("w2", (256, 128)), ("b2", (128,)))}
    results = []
    for count in (1, 2, 3, 4, 5, 7, 8, 9, 15, 16, 17, 659, 660):
        value = rng.uniform(-1, 1, (count, 128)).astype(np.float32)
        arrays = [value, np.full((count, 128), 12345.25, np.float32)]
        arrays += [weights[key] for key in ("w1", "b1", "w2", "b2")]
        guarded = [Guarded(a) for a in arrays]
        try:
            candidate.function(*[a.ptr for a in guarded], count)
            actual = guarded[1].array.reshape(value.shape)
            result = module.compare(baseline.forward("guard", value, weights), actual)
            if not result["pass"]:
                raise AssertionError("Bit mismatch with guarded minimum-sized buffers")
            result.update(tokens=count, trailing_pages="PAGE_NOACCESS", status="pass")
            results.append(result)
            print("PASS minimum-sized guarded buffers tokens=", count, flush=True)
        finally:
            for a in guarded:
                a.close()
    blocked = Guarded(np.ones(1, np.float32))
    try:
        inaccessible = ctypes.cast(blocked.base, FLOAT_PTR)
        for count in (0, 661, 4294967295):
            candidate.function(*([inaccessible] * 6), count)
            candidate.function(*([None] * 6), count)
            print("PASS invalid no-access/null buffers tokens=", count, flush=True)
    finally:
        blocked.close()
    with (HERE / "memory_guards.json").open("x", encoding="utf-8") as stream:
        json.dump({"status": "pass", "hardware_execution": False, "valid": results,
                   "invalid_noaccess_and_null": [0, 661, 4294967295]}, stream, indent=2)


if __name__ == "__main__":
    main()

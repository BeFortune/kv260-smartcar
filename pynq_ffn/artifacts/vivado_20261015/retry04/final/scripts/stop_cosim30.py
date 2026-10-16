"""Stop only this run's XSIM process tree after recording a declared scope change."""

import ctypes
from ctypes import wintypes
import datetime
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def main():
    stage = ROOT / "evidence/hls/cosim_small.command.json"
    record = json.loads(stage.read_text(encoding="utf-8"))
    if record["status"] != "running" or record["exit_code"] is not None:
        raise RuntimeError("Cosim already finished; inspect result instead of stopping")
    parent = int(record["pid"])
    query = "Get-CimInstance Win32_Process | Where-Object { $_.ProcessId -eq " + str(parent) + " -or $_.ParentProcessId -eq " + str(parent) + " } | Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
    result = subprocess.run(["powershell.exe", "-NoProfile", "-Command", query], capture_output=True, text=True, check=True)
    rows = json.loads(result.stdout)
    if isinstance(rows, dict):
        rows = [rows]
    parents = [row for row in rows if row["ProcessId"] == parent]
    if len(parents) != 1 or "cmd.exe" not in parents[0]["Name"].lower():
        raise RuntimeError("Unexpected stage parent process identity")
    if "hls_config_cosim_small.cfg" not in (parents[0]["CommandLine"] or ""):
        raise RuntimeError("Refusing to stop a process unrelated to this cosim")
    children = [row for row in rows if row["ParentProcessId"] == parent]
    if len(children) != 1:
        raise RuntimeError("Inspect process tree before stopping: " + str(children))
    child = children[0]
    if child["Name"].lower() not in ("cmd.exe", "vitis-run.exe", "loader.exe"):
        raise RuntimeError("Unexpected cosim child: " + str(child))
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, int(child["ProcessId"]))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    action = {"scope": "cosim_small only", "reason": "Declared further reduction from 30-token suite to complete 1-token smoke cosim; this run is NOT passed", "requested_at": datetime.datetime.now().astimezone().isoformat(), "verified_stage_parent": parents[0], "verified_subtree_root": child}
    destination = ROOT / "evidence/hls/cosim30_stop.json"
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(action, stream, indent=2)
        stream.write("\n")
    try:
        stopped = subprocess.run([r"C:\Windows\System32\taskkill.exe", "/PID", str(child["ProcessId"]), "/T", "/F"], capture_output=True, text=True)
        action.update(taskkill_exit_code=stopped.returncode, taskkill_stdout=stopped.stdout, taskkill_stderr=stopped.stderr)
        exit_code = wintypes.DWORD()
        if kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            action["subtree_root_exit_code_observed"] = exit_code.value
    finally:
        kernel.CloseHandle(handle)
    action["finished"] = datetime.datetime.now().astimezone().isoformat()
    destination.write_text(json.dumps(action, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(action, indent=2))
    if action["taskkill_exit_code"] != 0:
        raise RuntimeError("Cosim subtree stop did not complete cleanly")


if __name__ == "__main__":
    main()

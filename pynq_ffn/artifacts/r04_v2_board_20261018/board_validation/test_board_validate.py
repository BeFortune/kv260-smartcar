"""Offline-only fake MMIO/ORT tests. Mock results NEVER represent PL validation."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(PROJECT))
import board_validate as v
from ps.pynq_backend import PynqFFN, HardwareStateError

# Reuse the already-reviewed, CPU-only fake driver fixtures, never its main().
OLD = PROJECT / "artifacts/ffn_opt_20261018/board_scripts/pl0_retest_v3/test_board_validate.py"
spec = importlib.util.spec_from_file_location("previous_fake_fixtures", OLD)
old = importlib.util.module_from_spec(spec)
spec.loader.exec_module(old)


class Evidence:
    def __init__(self, directory):
        self.directory = directory
        self.events = []
        self.report = {}
    def event(self, name, data):
        self.events.append((name, data))


def weights():
    return {key: np.zeros(shape, np.float32) for key, shape in
            {"w1": (128,256), "b1": (256,), "w2": (256,128), "b2": (128,)}.items()}


def base_args(tmp_path):
    return ["--root", "root", "--deployment-manifest", "manifest", "--manifest-sha256", "0"*64,
            "--bit-sha256", "0"*64, "--hwh-sha256", "0"*64,
            "--system-acceptance", "acceptance", "--acceptance-sha256", "0"*64,
            "--output-dir", str(tmp_path/"result")]


@pytest.mark.parametrize("extra", [["--allow-pl"], ["--timeout-s", "nan"], ["--timeout-s", "0"],
                                    ["--allow-pl0-divisor0-15"], ["--expected-clock-mhz", "150"],
                                    ["--warmup", "0"], ["--threads", "1"]])
def test_cli_refuses_unsafe_or_unfair_overrides(tmp_path, extra):
    with pytest.raises(SystemExit):
        v.parse_args(base_args(tmp_path)+extra)


def test_cli_default_static_and_fixed_clock(tmp_path):
    args = v.parse_args(base_args(tmp_path))
    assert not args.allow_pl
    assert args.expected_clock_mhz == 100 and args.clock_tolerance_percent == .5


def test_no_hardware_writers_in_harness_or_gates():
    import ast
    for filename in ("board_validate.py", "safety_gates.py"):
        tree = ast.parse((HERE/filename).read_text(encoding="utf8"))
        forbidden = {"download", "set_pl_clk", "reset", "terminate", "kill", "freebuffer"}
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr in forbidden]
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                    and isinstance(n.ctx, ast.Store) and n.attr in {"DIVISOR0", "DIVISOR1", "fclk0_mhz"}]


def test_safe_cleanup_idle_then_close_then_ort(tmp_path):
    events = []
    ip = SimpleNamespace(starts=1, read=lambda _: (events.append("idle") or 4))
    backend = SimpleNamespace(close=lambda: events.append("free"))
    runtime = SimpleNamespace(sessions=SimpleNamespace(clear=lambda: events.append("ort")))
    v.safe_cleanup(Evidence(tmp_path), backend, ip, runtime)
    assert events == ["idle", "free", "ort"]


@pytest.mark.parametrize("case", ["poisoned_idle", "inflight_idle", "busy", "read_error", "close_error"])
def test_uncertainty_never_clears_ort_or_escapes_quarantine(tmp_path, monkeypatch, case):
    class Parked(BaseException): pass
    events = []
    def read(_):
        if case == "read_error": raise OSError("unknown")
        return 0 if case == "busy" else 4
    def close():
        if case == "close_error": raise HardwareStateError("uncertain close")
        pytest.fail("Uncertain buffer must never be closed")
    backend = SimpleNamespace(_poisoned=case=="poisoned_idle", _inflight=case=="inflight_idle", close=close)
    runtime = SimpleNamespace(sessions=SimpleNamespace(clear=lambda: pytest.fail("No ORT destruction before quarantine")))
    def quarantine(e, b, error):
        events.append("parked")
        assert b is backend and runtime in v.g.LIVE
        raise Parked()
    monkeypatch.setattr(v.g, "quarantine", quarantine)
    with pytest.raises(Parked):
        v.safe_cleanup(Evidence(tmp_path), backend, SimpleNamespace(read=read, starts=1), runtime, TimeoutError("timeout"))
    assert events == ["parked"]


def test_real_backend_fake_success_free_reverse_order_after_done(tmp_path):
    raw = old.MockIP()
    ip = v.g.CountedIP(raw)
    backend = PynqFFN(ip, old.metadata(), allocate_fn=raw.allocate)
    result = backend.forward("camera_0", np.ones((1,128),np.float32), weights())
    assert result.shape == (1,128) and ip.starts == 1
    freed = []
    for index, buf in enumerate(raw.buffers):
        buf.freebuffer = lambda i=index: freed.append(i)
    v.safe_cleanup(Evidence(tmp_path), backend, ip, None)
    assert freed == list(reversed(range(6)))


def test_real_backend_fake_timeout_idle_later_still_no_free(tmp_path, monkeypatch):
    raw = old.MockIP(complete=False)
    ip = v.g.CountedIP(raw)
    backend = PynqFFN(ip, old.metadata(), allocate_fn=raw.allocate, timeout_s=.001, poll_interval_s=0)
    with pytest.raises(TimeoutError):
        backend.forward("camera_0", np.ones((1,128),np.float32), weights())
    raw.status = 4  # An IDLE later must not override the backend's poison.
    class Parked(BaseException): pass
    def park(*a): raise Parked()
    monkeypatch.setattr(v.g,"quarantine",park)
    with pytest.raises(Parked):
        v.safe_cleanup(Evidence(tmp_path), backend, ip, None)
    assert not any(b.freed for b in raw.buffers)


def test_one_token_first_37_real_fake_starts_3_inputs(tmp_path):
    refs = tmp_path/"split_ort4/references"
    refs.mkdir(parents=True)
    all_weights = {name: weights() for name in v.g.LAYERS}
    for index in (0,58,116):
        cuts = {}
        for i,name in enumerate(v.g.LAYERS):
            n = 660 if name.startswith("camera") else 4
            x = np.full((n,128), index/100, np.float32)
            all_weights[name]["b2"][:] = i
            cuts["input_"+name],cuts["ffn_"+name] = x,x+i
        v.g.dump_npz(np,refs/f"frame_{index:03d}.npz",cuts)
    raw = old.MockIP()
    ip = v.g.CountedIP(raw)
    backend = PynqFFN(ip,old.metadata(),allocate_fn=raw.allocate)
    e = Evidence(tmp_path)
    result = v.layer_checks(np,backend,ip,all_weights,SimpleNamespace(run_root=tmp_path),e)
    assert result["layer_cases"] == 36 and result["distinct_record_inputs"] == 3
    assert ip.starts == 37
    assert e.events[0][0] == "one_token_smoke" and e.events[0][1]["kernel_starts"] == 1
    assert {row[1]["record_index"] for row in e.events if row[0]=="single_layer"} == {0,58,116}
    backend.close()


def test_parent_wait_survives_interrupt_and_evidence_failure():
    class Process:
        pid=123
        def __init__(self): self.n=0
        def wait(self):
            self.n+=1
            if self.n<=2: raise KeyboardInterrupt()
            return 0
    process=Process()
    e=SimpleNamespace(event=lambda *a: (_ for _ in ()).throw(OSError("full disk")))
    assert v.wait_without_cancellation(process,e) == 0 and process.n==3


def test_quarantine_child_actual_driver_timeout_never_exits_or_frees(tmp_path):
    # Child has ONLY numpy fake buffers. It is safe to kill THIS mock-only child
    # after proving retention; never run this test in a hardware-owning process.
    code = f'''import sys, pathlib, argparse, signal
sys.dont_write_bytecode=True
sys.path[:0]=[{str(HERE)!r},{str(PROJECT)!r}]
import board_validate as v
import importlib.util, numpy as np
s=importlib.util.spec_from_file_location('fake',{str(OLD)!r}); m=importlib.util.module_from_spec(s); s.loader.exec_module(m)
from ps.pynq_backend import PynqFFN
raw=m.MockIP(complete=False); ip=v.g.CountedIP(raw)
b=PynqFFN(ip,m.metadata(),allocate_fn=raw.allocate,timeout_s=.001,poll_interval_s=0)
e=v.g.Evidence(pathlib.Path({str(tmp_path/'quarantine')!r}),argparse.Namespace(mock_only=True))
v.protect_lifetime_signals()
w={{k:np.zeros(shape,np.float32) for k,shape in {{'w1':(128,256),'b1':(256,),'w2':(256,128),'b2':(128,)}}.items()}}
try: b.forward('camera_0',np.ones((1,128),np.float32),w)
except BaseException as error:
 def sleeper(seconds):
  assert not any(buf.freed for buf in raw.buffers)
  pathlib.Path({str(tmp_path/'retained')!r}).write_text('mock buffers retained')
  import time; time.sleep(.1)
 real=v.g.quarantine
 v.g.quarantine=lambda e,b,error: real(e,b,error,sleeper=sleeper)
 v.safe_cleanup(e,b,ip,None,error)
raise AssertionError('must never exit')
'''
    process=subprocess.Popen([sys.executable,"-B","-c",code],stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        deadline=time.monotonic()+15
        while not (tmp_path/"retained").exists() and process.poll() is None and time.monotonic()<deadline:
            time.sleep(.03)
        assert (tmp_path/"retained").exists(), process.communicate(timeout=2)
        time.sleep(.15)
        assert process.poll() is None
        report=json.loads((tmp_path/"quarantine/report.json").read_text(encoding="utf8"))
        assert report["failure_mode"] == "failed_quarantined" and report["kernel_starts_observed"] == 1
        if sys.platform != "win32":
            process.send_signal(signal.SIGTERM)
            process.send_signal(signal.SIGINT)
            time.sleep(.15)
            assert process.poll() is None
    finally:
        if process.poll() is None: process.kill()  # ONLY the fake-only child.
        process.communicate(timeout=5)


@pytest.mark.parametrize("frequency", [100.,99.999,150.])
def test_clock_snapshot_readonly_pll_cpu_and_frequency_guard(tmp_path,frequency):
    class Register:
        def __getitem__(self,key): return 1
    bank=SimpleNamespace(**{name:Register() for name in ("IOPLL_CTRL","RPLL_CTRL","APLL_CTRL","DPLL_CTRL","VPLL_CTRL","ACPU_CTRL")})
    clocks=SimpleNamespace(_instance=SimpleNamespace(_crl_registers=bank,_crf_registers=bank,
                            PL_CLK_CTRLS=[Register()]*4,get_pll_mhz=lambda r:1500.),fclk0_mhz=frequency,cpu_mhz=1200.)
    e=v.g.Evidence(tmp_path/"result",argparse.Namespace())
    ip=v.g.CountedIP(old.MockIP())
    args=SimpleNamespace(expected_clock_mhz=100.,clock_tolerance_percent=.5)
    if frequency==150.:
        with pytest.raises(v.g.ClockMismatchError): v.clock_snapshot(clocks,ip,args,e,"prestart")
    else:
        result=v.clock_snapshot(clocks,ip,args,e,"prestart")
        assert result["cpu_raw"] == {"ACPU_CTRL":1} and len(result["pll_raw"])==5
    assert ip.starts==0 and ip.writes==0


def test_static_fresh_process_real_bundle_no_project_or_pynq_import(tmp_path):
    root, old_argv=old.deployment(tmp_path)
    report=tmp_path/"system.log"; report.write_text("SYNTHETIC system report; NOT build success")
    a=tmp_path/"acceptance.json"
    bit=root/"overlay/ffn_overlay_wrapper.bit"
    v.write_json(a,{"schema":"r04v2.system_acceptance.v1","status":"已验证",
                    "bit_sha256":v.g.sha(bit),"hwh_sha256":v.g.sha(bit.with_suffix('.hwh')),
                    "evidence_files":{"system.log":v.g.sha(report)}})
    argv=old_argv+["--system-acceptance",str(a),"--acceptance-sha256",v.g.sha(a)]
    code=f'''import sys
sys.path.insert(0,{str(HERE)!r})
import board_validate as v
assert v.main({argv!r})==0
assert 'pynq' not in sys.modules and 'ps.runtime' not in sys.modules
'''
    process=subprocess.run([sys.executable,"-B","-c",code],capture_output=True,text=True,timeout=30)
    assert process.returncode==0,process.stderr
    value=json.loads((tmp_path/"result/report.json").read_text(encoding="utf8"))
    assert value["status"]=="已验证" and not value["hardware_execution"]
    with pytest.raises(FileExistsError): v.main(argv)
    # Corrupt the actual report after attestation; the acceptance file stays pinned.
    report.write_text("changed")
    with pytest.raises(ValueError,match="System report hash mismatch"):
        v.acceptance(v.parse_args(argv))


@pytest.mark.parametrize("field,value", [("status","未执行"),("bit_sha256","1"*64),
                                         ("schema","wrong"),("evidence_files",{})])
def test_acceptance_refuses_unverified_or_wrong_artifacts(tmp_path,field,value):
    args=v.parse_args(base_args(tmp_path))
    report=tmp_path/"report.txt"; report.write_text("mock")
    a=tmp_path/"acceptance.json"
    data={"schema":"r04v2.system_acceptance.v1","status":"已验证","bit_sha256":"0"*64,
          "hwh_sha256":"0"*64,"evidence_files":{"report.txt":v.g.sha(report)}}
    data[field]=value
    v.write_json(a,data)
    args.system_acceptance=str(a); args.acceptance_sha256=v.g.sha(a)
    with pytest.raises(ValueError): v.acceptance(args)


def test_benchmark_117_frames_warmup3_raw_timing(tmp_path):
    calls=[]
    class Runtime:
        def run(self,image):
            calls.append(image)
            self.last_timings_ms={"golden":.01,"total":.02}
            return {k:np.zeros((1,2),np.float32) for k in v.g.OUTPUTS}
    records=[SimpleNamespace(frame_index=i,csv_line=i) for i in range(117)]
    result=v.benchmark(np,Runtime(),records,lambda r:r.frame_index,None,SimpleNamespace(mode="golden4"),Evidence(tmp_path))
    assert len(calls)==120 and calls[:3]==[0,1,2]
    assert result["frames"]==117 and result["warmup"]==3 and result["spinning"] is None
    assert result["run_total_ms"]["n"]==117 and result["completion_interval_ms"]["n"]==116
    assert len(result["raw_samples"])==117 and result["sustained_offline_outputs_hz"]>0


def test_integer_boundary_not_hidden_by_float_tolerance():
    v.g.compare(np,np.array([1.00001]),np.array([.99999]),1e-3)
    with pytest.raises(AssertionError,match="Integer"):
        v.g.compare_integer(np,np.array([1.00001]),np.array([.99999]))


def test_production_runtime_still_exact_sha():
    assert v.g.sha(PROJECT/"ps/runtime.py")==v.RUNTIME_SHA


@pytest.mark.parametrize("mode", v.MODES)
def test_worker_mode_policy_and_cleanup_fake_only(tmp_path,monkeypatch,mode):
    from types import ModuleType
    args=v.parse_args(base_args(tmp_path))
    args.mode=mode; args.allow_pl=True; args.reuse_programmed_overlay=True
    args.recording="not opened"; args.run_root=str(tmp_path)
    captured=[]; closed=[]
    raw=old.MockIP()
    class Backend:
        def __init__(self,ip,regs,timeout_s): self.ip=ip
        def close(self): closed.append("backend")
    class Runtime:
        def __init__(self,bundle,**kwargs):
            captured.append(kwargs); self.weights={}
            self.sessions=SimpleNamespace(clear=lambda:closed.append("sessions"))
    monkeypatch.setattr(v,"static",lambda a:(tmp_path,None,old.metadata(),{}))
    monkeypatch.setattr(v,"require_target",lambda:None)
    def inputs(*a):
        a[-1].report["inputs"]={"digest":"fixed"}
        return np,Runtime,[],None,Backend,lambda r:None
    monkeypatch.setattr(v,"load_inputs",inputs)
    monkeypatch.setattr(v,"validate_reference_index",lambda a:None)
    fake=ModuleType("pynq"); fake.Overlay=object(); fake.PL=object(); fake.Clocks=object()
    monkeypatch.setitem(sys.modules,"pynq",fake)
    overlay=SimpleNamespace(ip_dict={"ffn_accel_0":{"registers":old.metadata()}},ffn_accel_0=raw)
    monkeypatch.setattr(v.g,"reuse_overlay",lambda *a:overlay)
    monkeypatch.setattr(v,"clock_snapshot",lambda *a:{"fclk0_mhz":100.})
    def layers(np_,backend,ip,*a): ip.starts+=37; return {"mock":True}
    monkeypatch.setattr(v,"layer_checks",layers)
    def bench(np_,runtime,records,preprocess,measured,*a):
        if measured: measured.backend.ip.starts+=480
        return {"mock":True}
    monkeypatch.setattr(v,"benchmark",bench)
    assert v.worker(args)==0
    assert captured[0]["threads"]==4
    assert captured[0]["spinning"] is (None if mode=="golden4" else False)
    assert captured[0]["mode"]=={"golden4":"golden","split_ort4":"split-ort","split_pl4":"split-numpy"}[mode]
    assert closed==(["backend","sessions"] if mode=="split_pl4" else ["sessions"])
    result=json.loads((tmp_path/"result/report.json").read_text(encoding="utf8"))
    assert result["status"]=="已验证"
    if mode=="split_pl4": assert result["kernel_starts_observed"]==517


def test_worker_150mhz_fails_before_backend_runtime_allocation(tmp_path,monkeypatch):
    from types import ModuleType
    args=v.parse_args(base_args(tmp_path)); args.mode="split_pl4"
    args.allow_pl=True; args.reuse_programmed_overlay=True
    monkeypatch.setattr(v,"static",lambda a:(tmp_path,None,old.metadata(),{}))
    monkeypatch.setattr(v,"require_target",lambda:None)
    def forbidden(*a,**kw): pytest.fail("No backend/runtime/allocate at 150MHz")
    monkeypatch.setattr(v,"load_inputs",lambda *a:(np,forbidden,[],None,forbidden,lambda r:None))
    monkeypatch.setattr(v,"validate_reference_index",lambda a:None)
    fake=ModuleType("pynq"); fake.Overlay=object(); fake.PL=object(); fake.Clocks=object()
    monkeypatch.setitem(sys.modules,"pynq",fake)
    raw=old.MockIP()
    overlay=SimpleNamespace(ip_dict={"ffn_accel_0":{"registers":old.metadata()}},ffn_accel_0=raw)
    monkeypatch.setattr(v.g,"reuse_overlay",lambda *a:overlay)
    def mismatch(*a): raise v.g.ClockMismatchError("150MHz")
    monkeypatch.setattr(v,"clock_snapshot",mismatch)
    assert v.worker(args)==1
    assert not raw.buffers and not raw.starts and not raw.regs


@pytest.mark.parametrize("bad", ["bit", "hwh", "timestamp", "sha1"])
def test_identity_checked_before_constructor_cannot_mask_loaded_state(tmp_path,monkeypatch,bad):
    from types import ModuleType
    import hashlib
    root,argv=old.deployment(tmp_path)
    bit=root/"overlay/ffn_overlay_wrapper.bit"
    args=SimpleNamespace(bit_sha256=v.g.sha(bit),hwh_sha256=v.g.sha(bit.with_suffix('.hwh')))
    state={"bitfile_name":str(bit),"timestamp":"original_timestamp","active_name":"MOCK",
           "bitfile_hash":hashlib.sha1(bit.read_bytes()).hexdigest()}
    pl=SimpleNamespace(bitfile_name=str(bit),timestamp="original_timestamp")
    if bad=="bit":
        wrong=tmp_path/"wrong.bit"; wrong.write_bytes(b"WRONG"); pl.bitfile_name=str(wrong)
    if bad=="hwh": bit.with_suffix('.hwh').write_text("tampered")
    if bad=="timestamp": pl.timestamp="different"
    if bad=="sha1": state["bitfile_hash"]="0"*40
    (tmp_path/"global_pl_state.json").write_text(json.dumps(state),encoding="utf8")
    server=ModuleType("pynq.pl_server"); global_state=ModuleType("pynq.pl_server.global_state")
    global_state.STATE_DIR=str(tmp_path); server.global_state=global_state
    monkeypatch.setitem(sys.modules,"pynq.pl_server",server)
    monkeypatch.setitem(sys.modules,"pynq.pl_server.global_state",global_state)
    def masking_constructor(*a,**kw):
        pytest.fail("Identity must fail BEFORE constructor overwrites device.bitfile_name")
    with pytest.raises(RuntimeError):
        v.g.reuse_overlay(masking_constructor,pl,args,Evidence(tmp_path))


def test_identity_matching_only_constructs_download_false(tmp_path,monkeypatch):
    from types import ModuleType
    import hashlib
    root,argv=old.deployment(tmp_path); bit=root/"overlay/ffn_overlay_wrapper.bit"
    state={"bitfile_name":str(bit),"timestamp":"original","active_name":"MOCK",
           "bitfile_hash":hashlib.sha1(bit.read_bytes()).hexdigest()}
    (tmp_path/"global_pl_state.json").write_text(json.dumps(state),encoding="utf8")
    server=ModuleType("pynq.pl_server"); global_state=ModuleType("pynq.pl_server.global_state")
    global_state.STATE_DIR=str(tmp_path); server.global_state=global_state
    monkeypatch.setitem(sys.modules,"pynq.pl_server",server)
    monkeypatch.setitem(sys.modules,"pynq.pl_server.global_state",global_state)
    sentinel=object()
    def constructor(path,*,download):
        assert Path(path)==bit and download is False
        return sentinel
    args=SimpleNamespace(bit_sha256=v.g.sha(bit),hwh_sha256=v.g.sha(bit.with_suffix('.hwh')))
    e=Evidence(tmp_path)
    assert v.g.reuse_overlay(constructor,SimpleNamespace(bitfile_name=str(bit),timestamp="original"),args,e) is sentinel
    assert e.report["overlay_loads"]==0 and not e.report["hardware_programmed"]


def test_parent_launch_modes_in_order_with_reference_hashes(tmp_path,monkeypatch):
    args=base_args(tmp_path)+["--allow-pl","--reuse-programmed-overlay","--recording","unused"]
    monkeypatch.setattr(v,"static",lambda a:(tmp_path,None,{},{}))
    monkeypatch.setattr(v,"require_target",lambda:None)
    calls=[]
    def launch(a,mode,e,digest,index):
        if calls: assert index.is_file() and digest=="fixed_dataset"
        else: assert index is None and digest is None
        calls.append(mode)
        out=e.directory/mode; (out/"references").mkdir(parents=True)
        v.write_json(out/"report.json",{"status":"已验证"})
        return {"status":"已验证","inputs":{"digest":"fixed_dataset"},
                "phases":{"performance":{"mock_only":True}}}
    monkeypatch.setattr(v,"launch",launch)
    assert v.main(args)==0
    assert calls==list(v.MODES)


def test_runtime_sha_mismatch_rejected_even_when_external_manifest_updates(tmp_path):
    root,argv=old.deployment(tmp_path)
    (root/"ps/runtime.py").write_text("raise AssertionError('not executed')",encoding="utf8")
    manifest=tmp_path/"external.json"
    value=json.loads(manifest.read_text())
    value["files"]["ps/runtime.py"]={"bytes":(root/"ps/runtime.py").stat().st_size,"sha256":v.g.sha(root/"ps/runtime.py")}
    manifest.write_text(json.dumps(value))
    argv[argv.index("--manifest-sha256")+1]=v.g.sha(manifest)
    argv += ["--system-acceptance","unused","--acceptance-sha256","0"*64]
    with pytest.raises(ValueError,match="Production Runtime SHA changed"):
        v.static(v.parse_args(argv))



def test_actual_runtime_cpu_only_golden_split_and_numpy_real_weights(tmp_path):
    # Real unchanged ONNX/weights and synthetic input, NOT board execution.
    from ps.runtime import Runtime
    bundle=PROJECT/"artifacts/bundle_20261014"
    image=np.zeros((1,3,3,66,200),np.float32)
    outputs={}; cuts={}
    for mode,spin in (("golden",None),("split-ort",False),("split-numpy",False)):
        runtime=Runtime(bundle,mode=mode,threads=4,spinning=spin)
        outputs[mode]=runtime.run(image)
        cuts[mode]={k:x.copy() for k,x in runtime.last_ffn_outputs.items()}
        runtime.sessions.clear(); del runtime
    for mode in ("split-ort","split-numpy"):
        for name in v.g.OUTPUTS:
            v.g.compare(np,outputs[mode][name],outputs["golden"][name],1e-3)
        v.g.compare_integer(np,outputs[mode]["speed_raw"],outputs["golden"]["speed_raw"])
    for name in v.g.LAYERS:
        v.g.compare(np,cuts["split-numpy"][name],cuts["split-ort"][name],1e-3)

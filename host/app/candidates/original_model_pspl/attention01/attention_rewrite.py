"""Strictly source-pinned camera batch-first layout rewrite; no model generalization."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import numpy as np
import onnx
from onnx import helper, numpy_helper

HERE = Path(__file__).resolve().parent
WORKSPACE = HERE.parents[4]
SOURCE_PATH = WORKSPACE / "ros_craic_car/src/base_control/resource/model_current.onnx"
SOURCE_SHA256 = "ad58d1fc9168da94b9ce076ba8cc9754c371ead87974da8bc9cc601ad1650d5b"
OUTPUT_NAMES = ["speed_norm", "speed_raw", "region_logits", "region_probs"]


def load_checked(content: bytes):
    if hashlib.sha256(content).hexdigest() != SOURCE_SHA256:
        raise ValueError("Unreviewed source SHA-256; only the pinned NUC source is supported")
    model = onnx.load_model_from_string(content)
    onnx.checker.check_model(model)
    return model


def validate_topology(model):
    """Reject any node/edge/attribute/initializer drift from the source-pinned graph."""
    reviewed = load_checked(SOURCE_PATH.read_bytes())
    if model.graph.SerializeToString() != reviewed.graph.SerializeToString():
        raise ValueError("Unexpected topology or initializer identity; reviewed graph only")
    if [v.name for v in model.graph.input] != ["images"] or [v.name for v in model.graph.output] != OUTPUT_NAMES:
        raise ValueError("Unexpected topology: input/output contract")
    if any(t.data_location == onnx.TensorProto.EXTERNAL for t in model.graph.initializer):
        raise ValueError("Unexpected topology: external initializers")
    return reviewed


def _node_record(node):
    attributes = {}
    for attr in node.attribute:
        value = helper.get_attribute_value(attr)
        if isinstance(value, onnx.TensorProto):
            value = {"dtype": value.data_type, "shape": list(value.dims),
                     "value": numpy_helper.to_array(value).tolist()}
        elif isinstance(value, bytes):
            value = value.decode("utf-8")
        attributes[attr.name] = value
    return {"name": node.name, "op": node.op_type, "inputs": list(node.input),
            "outputs": list(node.output), "attributes": attributes,
            "proto_sha256": hashlib.sha256(node.SerializeToString()).hexdigest()}


def _rewrite_block(model, index):
    p = f"/model/camera_encoder/layers.{index}/self_attn"
    block = f"/model/camera_encoder/layers.{index}"
    all_nodes = list(model.graph.node)
    positions = {n.name: i for i, n in enumerate(all_nodes)}
    first, last = positions[p + "/Transpose"], positions[p + "/Transpose_7"]
    selected = all_nodes[first:last + 1]
    if len(selected) != 67 or any(not n.name.startswith(p + "/") for n in selected):
        raise ValueError("Unexpected camera attention topology")
    old = {n.name[len(p) + 1:]: n for n in selected}
    new = []
    provenance = {}

    def append(node, sources, reason):
        new.append(node)
        provenance[node.name] = {"source_nodes": [p + "/" + s for s in sources], "reason": reason}
        return node.output[0]

    def clone(name, *, inputs=None, outputs=None, reason="byte-identical arithmetic node"):
        node = copy.deepcopy(old[name])
        if inputs is not None:
            node.input[:] = inputs
        if outputs is not None:
            node.output[:] = outputs
        return append(node, [name], reason)

    def constant(name, values, sources):
        output = p + "/batch_first/" + name
        append(helper.make_node("Constant", [], [output], name=output,
                                value=numpy_helper.from_array(np.asarray(values, dtype=np.int64))),
               sources, "new non-learned fixed shape constant; B=3,L=220,H=4,D=32")
        return output

    mm = clone("MatMul", inputs=[block + "/norm1/LayerNormalization_output_0", old["MatMul"].input[1]],
               reason="project unchanged weights directly on [B,L,C], not [L,B,C]")
    bias = clone("Add", reason="unchanged QKV bias on batch-first projection")
    shape = constant("qkv_shape", [3, 220, 3, 4, 32], ["Concat", "Constant_10", "Constant_13"])
    packed = append(helper.make_node("Reshape", [bias, shape], [p + "/batch_first/qkv_5d"],
                                    name=p + "/batch_first/ReshapeQKV", allowzero=0),
                    ["Reshape_2", "Unsqueeze"], "[B,L,3,H,D], all 660 camera tokens retained")
    packed = append(helper.make_node("Transpose", [packed], [p + "/batch_first/qkv_heads"],
                                    name=p + "/batch_first/TransposeQKV", perm=[2, 0, 3, 1, 4]),
                    ["Transpose_1", "Transpose_2", "Transpose_3", "Transpose_4"],
                    "one pack permutation to [3,B,H,L,D]; gather directly yields [B,H,L,D]")
    for gather, head in [("Gather", "Reshape_6"), ("Gather_1", "Reshape_7"), ("Gather_2", "Reshape_8")]:
        clone(gather, inputs=[packed, old[gather].input[1]], outputs=list(old[head].output),
              reason="same Q/K/V scalar selector, directly expose [B,H,L,D]")
    # Keep the exact original Q and K separate square-root scaling BEFORE QK MatMul.
    # No scale folding, reduction-axis change, Softmax replacement, or token slicing.
    core_start = positions[p + "/Shape_1"]
    core_end = positions[p + "/MatMul_2"]
    for node in all_nodes[core_start:core_end + 1]:
        clone(node.name[len(p) + 1:])
    context = append(helper.make_node("Transpose", [old["MatMul_2"].output[0]],
                                     [p + "/batch_first/context_blhd"],
                                     name=p + "/batch_first/TransposeContext", perm=[0, 2, 1, 3]),
                     ["Transpose_6"], "[B,H,L,D] to [B,L,H,D], without sequence-first detour")
    flat = constant("projection_shape", [660, 128], ["Constant_19"])
    append(helper.make_node("Reshape", [context, flat], list(old["Reshape_9"].output),
                            name=p + "/batch_first/ReshapeProjection", allowzero=0),
           ["Reshape_9"], "flatten batch and sequence only; preserve C/H/D order")
    clone("Gemm", reason="unchanged out_proj Gemm, weight bytes and transB=1 retained")
    output_shape = constant("output_shape", [3, 220, 128], ["Constant_20"])
    append(helper.make_node("Reshape", [old["Gemm"].output[0], output_shape], list(old["Transpose_7"].output),
                            name=p + "/batch_first/RestoreBLC", allowzero=0),
           ["Reshape_10", "Transpose_7"], "restore [B,L,C] for unchanged residual and FFN")
    original_consumers = [(n.name, x) for i, n in enumerate(all_nodes) if not first <= i <= last
                          for x in n.input if x in {v for n in selected for v in n.output}]
    if {x for _, x in original_consumers} != {old["Transpose_7"].output[0]}:
        raise ValueError("Unexpected topology: camera attention intermediate escapes block")
    actions = []
    new_by_name = {n.name: n for n in new}
    for node in selected:
        replacements = [name for name, item in provenance.items() if node.name in item["source_nodes"]]
        identical = node.name in new_by_name and node.SerializeToString() == new_by_name[node.name].SerializeToString()
        actions.append({"before": _node_record(node),
                        "action": "retained_byte_identical" if identical else "rewritten" if replacements else "removed_layout_shape_scaffolding",
                        "replacement_nodes": replacements})
    model.graph.ClearField("node")
    model.graph.node.extend(all_nodes[:first] + new + all_nodes[last + 1:])
    return {"camera": index, "before_count": len(selected), "after_count": len(new),
            "node_rewrites": actions,
            "new_nodes": [{**_node_record(n), **provenance[n.name]} for n in new]}


def convert(content: bytes, *, blocks=(0,)):
    if tuple(blocks) not in ((0,), (0, 1)):
        raise ValueError("Only the first camera block or the fixed pair is supported")
    original = load_checked(content)
    validate_topology(original)
    candidate = copy.deepcopy(original)
    # Source has no required intermediate annotations. Fresh inference avoids stale layout shapes.
    candidate.graph.ClearField("value_info")
    rewrites = [_rewrite_block(candidate, index) for index in blocks]
    original_ids = {t.name: t.SerializeToString() for t in original.graph.initializer}
    candidate_ids = {t.name: t.SerializeToString() for t in candidate.graph.initializer}
    if original_ids != candidate_ids:
        raise ValueError("Initializer byte identity failed")
    onnx.checker.check_model(candidate, full_check=True)
    audit = {
        "schema": "pynq_ffn.camera_batch_first_rewrites.v1", "status": "已验证",
        "source_sha256": SOURCE_SHA256,
        "candidate_sha256": hashlib.sha256(candidate.SerializeToString()).hexdigest(),
        "blocks": list(blocks), "weights_byte_identical": True,
        "initializers": [{"name": tensor.name, "dtype": tensor.data_type, "shape": list(tensor.dims),
                          "before_proto_sha256": hashlib.sha256(tensor.SerializeToString()).hexdigest(),
                          "after_proto_sha256": hashlib.sha256(candidate_ids[tensor.name]).hexdigest(),
                          "data_sha256": hashlib.sha256(numpy_helper.to_array(tensor).tobytes()).hexdigest()}
                         for tensor in original.graph.initializer],
        "rewrites": rewrites, "hardware_execution": False,
        "preserved": ["FP32 original learned weights", "four final outputs", "all 660 camera K/V tokens",
                      "all four cross queries", "separate Q/K scaling before QK", "Softmax axis and reduction order",
                      "LayerNorm/GELU", "cross attention and all FFNs unchanged"],
    }
    return candidate, audit


def _load_partition():
    """Reuse the reviewed source loader/specs; never monkey-patch its source SHA."""
    spec = importlib.util.spec_from_file_location("attention01_fixed_partition", WORKSPACE / "pynq_ffn/ps/partition.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if module.MODEL_SHA256 != SOURCE_SHA256:
        raise ValueError("Reviewed partition source SHA changed")
    return module


def split_camera(candidate):
    partition = _load_partition()
    # Validate unchanged FFNs against exact reviewed nodes, including activation edges/constants.
    original = load_checked(partition.load_source(model=SOURCE_PATH))
    original_nodes = {n.name: n.SerializeToString() for n in original.graph.node}
    nodes = list(candidate.graph.node)
    specs = partition.layer_specs()[:2]
    producer = {out: i for i, node in enumerate(nodes) for out in node.output}
    cuts = []
    for spec in specs:
        first = producer[spec["input"]] + 1
        last = producer[spec["output"]]
        selected = nodes[first:last + 1]
        if len(selected) != 12 or any(n.SerializeToString() != original_nodes.get(n.name) for n in selected):
            raise ValueError("Camera FFN topology or exact FP32 activation changed")
        cuts.append(list(range(first, last + 1)))
    # Infer all shape metadata afresh but serialize the unoptimized full candidate separately.
    inferred = onnx.shape_inference.infer_shapes(candidate, strict_mode=True, data_prop=True)
    values = {v.name: v for v in list(inferred.graph.input) + list(inferred.graph.value_info) + list(inferred.graph.output)}
    initializers = {t.name: t for t in candidate.graph.initializer}
    arrays = {name: numpy_helper.to_array(t) for name, t in initializers.items()}
    weights = {}
    for spec in specs:
        for boundary in (spec["input"], spec["output"], spec["residual"]):
            values[boundary] = helper.make_tensor_value_info(boundary, onnx.TensorProto.FLOAT, spec["shape"])
        for key, shape in [("w1", (128, 256)), ("b1", (256,)), ("w2", (256, 128)), ("b2", (128,))]:
            array = arrays[spec["initializers"][key]]
            if array.shape != shape or array.dtype != np.float32 or not np.isfinite(array).all():
                raise ValueError("Camera FFN weight contract changed")
            weights[spec["name"] + "." + key] = np.ascontiguousarray(array)
    groups = [("stage_0", list(range(0, cuts[0][0])), None),
              ("camera_0", cuts[0], specs[0]),
              ("stage_1", list(range(cuts[0][-1] + 1, cuts[1][0])), None),
              ("camera_1", cuts[1], specs[1]),
              ("stage_2", list(range(cuts[1][-1] + 1, len(nodes))), None)]
    consumers = {}
    for i, node in enumerate(nodes):
        for name in node.input:
            consumers.setdefault(name, set()).add(i)
    artifacts, schedule = {}, []
    for name, indices, spec in groups:
        selected = [nodes[i] for i in indices]
        owned = set(indices)
        produced_order = [out for n in selected for out in n.output]
        produced = set(produced_order)
        inputs = list(dict.fromkeys(v for n in selected for v in n.input if v and v not in produced and v not in initializers))
        outputs = [v for v in produced_order if v in OUTPUT_NAMES or any(i not in owned for i in consumers.get(v, set()))]
        if spec and (inputs != [spec["input"]] or outputs != [spec["output"]]):
            raise ValueError("Camera FFN split leaks intermediates")
        if any(v not in values for v in inputs + outputs):
            raise ValueError("Missing shape metadata for split boundary")
        graph = partition._make_graph(candidate, selected, inputs, outputs, values, initializers, name)
        onnx.checker.check_model(graph, full_check=True)
        filename = name + ".onnx"
        artifacts[filename] = graph.SerializeToString()
        schedule.append({"name": name, "kind": "ffn" if spec else "non_ffn", "file": filename,
                         "inputs": inputs, "outputs": outputs,
                         "execution_role": "prepared_camera_backend" if spec else "CPU_PS"})
    available = {"images"}
    for item in schedule:
        if not set(item["inputs"]).issubset(available):
            raise ValueError("Unresolved split dependency")
        available.update(item["outputs"])
    if not set(OUTPUT_NAMES).issubset(available):
        raise ValueError("Final outputs missing from split")
    boundaries = {v for item in schedule for v in item["inputs"] + item["outputs"]}
    metadata = {}
    for name in sorted(boundaries):
        info = values[name].type.tensor_type
        metadata[name] = {"onnx_dtype": info.elem_type,
                          "shape": [d.dim_value if d.HasField("dim_value") else d.dim_param for d in info.shape.dim]}
    abi = {"schema": "pynq_ffn.camera_only_prepared.abi.v1", "dtype": "float32",
           "layout": "C-contiguous row-major right-multiply",
           "activation": "0.5*z*(1+erf(z/float32(sqrt(2))))", "layer_specs": specs,
           "weight_order": ["w1", "b1", "w2", "b2"],
           "weight_shapes": {"w1": [128, 256], "b1": [256], "w2": [256, 128], "b2": [128]},
           "weight_keys": {spec["name"]: {key: spec["name"] + "." + key for key in ("w1", "b1", "w2", "b2")} for spec in specs},
           "schedule": schedule, "tensors": metadata, "outputs": OUTPUT_NAMES,
           "source_sha256": SOURCE_SHA256, "candidate_sha256": hashlib.sha256(candidate.SerializeToString()).hexdigest(),
           "prepared_backend_interface": "prepare(layer,weights) once at init; run(layer,x) per inference",
           "cross_ffn_execution": "CPU, both original cross FFNs included in stage_2.onnx",
           "old_runtime_compatible": False, "hardware_execution": False}
    return artifacts, weights, abi


def _dump(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def export_bundle(output_dir):
    directory = Path(output_dir).resolve()
    if directory.exists():
        raise FileExistsError("Refusing an existing evidence directory")
    if HERE not in directory.parents:
        raise ValueError("Output must be confined to attention01")
    content = _load_partition().load_source(model=SOURCE_PATH)
    before = hashlib.sha256(content).hexdigest()
    first_block, first_audit = convert(content, blocks=(0,))
    candidate, audit = convert(content, blocks=(0, 1))
    artifacts, weights, abi = split_camera(candidate)
    artifacts.update({"golden.onnx": content, "single_camera_full.onnx": first_block.SerializeToString(),
                      "full.onnx": candidate.SerializeToString()})
    directory.mkdir(parents=True, exist_ok=False)
    for filename, data in artifacts.items():
        with (directory / filename).open("xb") as stream:
            stream.write(data)
    with (directory / "weights.npz").open("xb") as stream:
        np.savez(stream, **weights)
    _dump(directory / "ABI.json", abi)
    _dump(directory / "rewrites.json", audit)
    _dump(directory / "single_camera_rewrites.json", first_audit)
    after = hashlib.sha256(SOURCE_PATH.read_bytes()).hexdigest()
    if before != after or after != SOURCE_SHA256:
        raise ValueError("Original source changed during export")
    manifest = {"schema": "pynq_ffn.camera_only_prepared.bundle.v1", "status": "已验证",
                "candidate_promotion_status": "未执行", "source_sha256": SOURCE_SHA256,
                "source_sha256_before": before, "source_sha256_after": after,
                "source": str(SOURCE_PATH), "candidate_sha256": audit["candidate_sha256"],
                "weights_byte_identical": True, "old_runtime_compatible": False,
                "weight_order": abi["weight_order"], "weight_keys": abi["weight_keys"],
                "split": "3 CPU PS stages + 2 camera FFNs; both cross FFNs remain in CPU stage_2",
                "files": {p.name: {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
                          for p in sorted(directory.iterdir()) if p.is_file()},
                "hardware_execution": False}
    _dump(directory / "manifest.json", manifest)
    return manifest


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Pinned NUC camera-attention batch-first rewrite, offline only")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = export_bundle(args.output)
    print(json.dumps({"status": manifest["status"], "source_sha256": SOURCE_SHA256,
                      "candidate_sha256": manifest["candidate_sha256"], "bundle": str(args.output)}, indent=2))


if __name__ == "__main__":
    main()

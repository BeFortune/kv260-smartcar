from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

import numpy as np
import onnx
from onnx import helper, numpy_helper

MODEL_SHA256 = "ad58d1fc9168da94b9ce076ba8cc9754c371ead87974da8bc9cc601ad1650d5b"
MODEL_MEMBER = "model/model_current.onnx"
INPUT_SHAPE = [1, 3, 3, 66, 200]
OUTPUT_NAMES = ["speed_norm", "speed_raw", "region_logits", "region_probs"]


def layer_specs():
    result = []
    for kind, index, w1, w2 in [("camera", 0, 928, 929), ("camera", 1, 969, 970),
                               ("cross", 0, 1033, 1034), ("cross", 1, 1087, 1088)]:
        camera = kind == "camera"
        prefix = f"/model/camera_encoder/layers.{index}" if camera else f"/model/cross_blocks.{index}"
        bias_prefix = f"model.camera_encoder.layers.{index}" if camera else f"model.cross_blocks.{index}.ffn"
        result.append({
            "name": f"{kind}_{index}",
            "input": prefix + ("/norm2/LayerNormalization_output_0" if camera else "/ffn_norm/LayerNormalization_output_0"),
            "output": prefix + ("/linear2/Add_output_0" if camera else "/ffn/ffn.3/Add_output_0"),
            "residual": prefix + "/Add_output_0",
            "shape": [3, 220, 128] if camera else [1, 4, 128],
            "initializers": {"w1": f"onnx::MatMul_{w1}", "w2": f"onnx::MatMul_{w2}",
                             "b1": bias_prefix + (".linear1.bias" if camera else ".0.bias"),
                             "b2": bias_prefix + (".linear2.bias" if camera else ".3.bias")},
        })
    return result


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_source(*, model=None, payload=None):
    if (model is None) == (payload is None):
        raise ValueError("Choose exactly one model or payload")
    if payload is not None:
        with zipfile.ZipFile(payload) as archive:
            matches = [i for i in archive.infolist() if i.filename == MODEL_MEMBER]
            if len(matches) != 1 or matches[0].file_size != 2779255:
                raise ValueError("Unexpected or duplicate reviewed model member")
            content = archive.read(matches[0])
    else:
        path = Path(model)
        if path.stat().st_size != 2779255:
            raise ValueError("Unexpected reviewed model size")
        content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != MODEL_SHA256:
        raise ValueError("Model SHA-256 does not match the reviewed model")
    return content


def _attribute(node, name):
    return next((helper.get_attribute_value(a) for a in node.attribute if a.name == name), None)


def _make_graph(source, nodes, inputs, outputs, values, initializers, name):
    used = {x for n in nodes for x in n.input}
    graph = helper.make_graph(nodes, name, [values[x] for x in inputs], [values[x] for x in outputs],
                              [t for key, t in initializers.items() if key in used])
    result = helper.make_model(graph, opset_imports=list(source.opset_import), producer_name="pynq_ffn.fixed_partition")
    result.ir_version = source.ir_version
    onnx.checker.check_model(result)
    return result


def build_bundle(output_dir, *, model=None, payload=None):
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError("Refusing an existing bundle directory")
    content = load_source(model=model, payload=payload)
    original = onnx.load_model_from_string(content)
    if [v.name for v in original.graph.input] != ["images"] or [v.name for v in original.graph.output] != OUTPUT_NAMES:
        raise ValueError("Unexpected final model contract")
    if any(t.data_location == onnx.TensorProto.EXTERNAL for t in original.graph.initializer):
        raise ValueError("External weights are not supported")
    inferred = onnx.shape_inference.infer_shapes(original, strict_mode=True, data_prop=True)
    nodes = list(inferred.graph.node)
    initializers = {t.name: t for t in inferred.graph.initializer}
    arrays = {k: numpy_helper.to_array(v) for k, v in initializers.items()}
    values = {v.name: v for v in list(inferred.graph.input) + list(inferred.graph.value_info) + list(inferred.graph.output)}
    producer = {out: i for i, n in enumerate(nodes) for out in n.output}
    specs = layer_specs()
    ffn_indices = []
    weights = {}
    expected_ops = ["MatMul", "Add", "Constant", "Div", "Erf", "Constant", "Add", "Mul", "Constant", "Mul", "MatMul", "Add"]
    for spec in specs:
        first = producer[spec["input"]] + 1
        last = producer[spec["output"]]
        indices = list(range(first, last + 1))
        selected = [nodes[i] for i in indices]
        norm = nodes[first - 1]
        residual = nodes[last + 1]
        if norm.op_type != "LayerNormalization" or norm.input[0] != spec["residual"] or _attribute(norm, "axis") != -1 or not np.isclose(_attribute(norm, "epsilon"), 1e-5):
            raise ValueError("FFN pre-normalization boundary changed")
        if residual.op_type != "Add" or list(residual.input) != [spec["residual"], spec["output"]]:
            raise ValueError("FFN external residual boundary changed")
        for boundary in (spec["input"], spec["output"], spec["residual"]):
            info = values[boundary].type.tensor_type
            dimensions = info.shape.dim
            if info.elem_type != onnx.TensorProto.FLOAT or len(dimensions) != 3:
                raise ValueError("Unexpected FFN boundary type")
            if any(d.HasField("dim_value") and d.dim_value != expected for d, expected in zip(dimensions, spec["shape"])):
                raise ValueError("Unexpected FFN boundary shape")
        if [n.op_type for n in selected] != expected_ops:
            raise ValueError(f"Unexpected FFN topology: {spec['name']}")
        produced = {v for n in selected for v in n.output}
        external = {v for n in selected for v in n.input if v and v not in produced and v not in initializers}
        if external != {spec["input"]}:
            raise ValueError("FFN includes residual or another external dependency")
        ids = spec["initializers"]
        if list(selected[0].input) != [spec["input"], ids["w1"]] or list(selected[10].input) != [selected[9].output[0], ids["w2"]]:
            raise ValueError("FFN matrix direction changed")
        if set(selected[1].input) != {ids["b1"], selected[0].output[0]} or set(selected[11].input) != {ids["b2"], selected[10].output[0]}:
            raise ValueError("FFN bias mapping changed")
        # Validate every activation edge, not just the presence of an Erf node.
        edges = {3: [selected[1].output[0], selected[2].output[0]],
                 4: [selected[3].output[0]], 6: [selected[4].output[0], selected[5].output[0]],
                 7: [selected[1].output[0], selected[6].output[0]],
                 9: [selected[7].output[0], selected[8].output[0]]}
        if any(list(selected[i].input) != expected for i, expected in edges.items()):
            raise ValueError("Unexpected GELU dataflow")
        for i, expected in [(2, np.float32(np.sqrt(2))), (5, np.float32(1)), (8, np.float32(.5))]:
            constant = numpy_helper.to_array(_attribute(selected[i], "value"))
            if constant.dtype != np.float32 or constant.shape != () or constant.item() != expected:
                raise ValueError("GELU constant changed")
        for key, shape in [("w1", (128, 256)), ("b1", (256,)), ("w2", (256, 128)), ("b2", (128,))]:
            array = arrays[ids[key]]
            if array.dtype != np.float32 or array.shape != shape or not np.isfinite(array).all():
                raise ValueError("Unexpected FFN weights")
            weights[spec["name"] + "." + key] = np.ascontiguousarray(array)
        for name in (spec["input"], spec["output"], spec["residual"]):
            values[name] = helper.make_tensor_value_info(name, onnx.TensorProto.FLOAT, spec["shape"])
        ffn_indices.append(indices)
    if [d.dim_value for d in values["images"].type.tensor_type.shape.dim] != INPUT_SHAPE:
        raise ValueError("Unexpected image shape")

    groups = []
    start = 0
    for spec, indices in zip(specs, ffn_indices):
        groups.append((f"stage_{len(groups)//2}", list(range(start, indices[0])), None))
        groups.append((spec["name"], indices, spec))
        start = indices[-1] + 1
    groups.append(("stage_4", list(range(start, len(nodes))), None))
    consumers = {}
    for i, node in enumerate(nodes):
        for name in node.input:
            consumers.setdefault(name, set()).add(i)
    artifacts = {"model.onnx": content}
    schedule = []
    for name, indices, spec in groups:
        selected = [nodes[i] for i in indices]
        owned = set(indices)
        produced_order = [out for n in selected for out in n.output]
        produced = set(produced_order)
        inputs = list(dict.fromkeys(v for n in selected for v in n.input if v and v not in produced and v not in initializers))
        outputs = [v for v in produced_order if v in OUTPUT_NAMES or any(i not in owned for i in consumers.get(v, set()))]
        if spec and (inputs != [spec["input"]] or outputs != [spec["output"]]):
            raise ValueError("FFN split leaks intermediates")
        if any(v not in values for v in inputs + outputs):
            raise ValueError("Missing shape metadata for stage boundary")
        filename = name + ".onnx"
        artifacts[filename] = _make_graph(inferred, selected, inputs, outputs, values, initializers, name).SerializeToString()
        schedule.append({"name": name, "kind": "ffn" if spec else "non_ffn", "file": filename,
                         "inputs": inputs, "outputs": outputs})
    available = {"images"}
    for item in schedule:
        if not set(item["inputs"]).issubset(available):
            raise ValueError("Unresolved stage dependency")
        available.update(item["outputs"])
    if not set(OUTPUT_NAMES).issubset(available):
        raise ValueError("Final outputs missing")
    boundary_names = {v for s in schedule for v in s["inputs"] + s["outputs"]}
    tensor_metadata = {}
    for name in sorted(boundary_names):
        tensor = values[name].type.tensor_type
        tensor_metadata[name] = {"onnx_dtype": tensor.elem_type,
                                 "shape": [d.dim_value if d.HasField("dim_value") else d.dim_param for d in tensor.shape.dim]}
    output_dir.mkdir(parents=True, exist_ok=False)
    for filename, data in artifacts.items():
        with (output_dir / filename).open("xb") as stream:
            stream.write(data)
    with (output_dir / "weights.npz").open("xb") as stream:
        np.savez(stream, **weights)
    abi = {"schema": "pynq_ffn.abi.v1", "dtype": "float32", "layout": "C-contiguous row-major right-multiply",
           "activation": "0.5*z*(1+erf(z/float32(sqrt(2))))", "layer_specs": specs,
           "weight_shapes": {"w1": [128, 256], "b1": [256], "w2": [256, 128], "b2": [128]},
           "schedule": schedule, "tensors": tensor_metadata, "outputs": OUTPUT_NAMES,
           "hardware_execution": False}
    with (output_dir / "ABI.json").open("x", encoding="utf8") as stream:
        json.dump(abi, stream, indent=2)
    manifest = {"schema": "pynq_ffn.bundle.v1", "source_sha256": MODEL_SHA256,
                "source": str(Path(model or payload).resolve()), "source_member": MODEL_MEMBER if payload else None,
                "files": {p.name: {"sha256": digest(p), "bytes": p.stat().st_size} for p in sorted(output_dir.iterdir())}}
    with (output_dir / "manifest.json").open("x", encoding="utf8") as stream:
        json.dump(manifest, stream, indent=2)
    return manifest

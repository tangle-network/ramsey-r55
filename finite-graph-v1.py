#!/usr/bin/env python3
"""Evaluate an exact forbidden-set objective for a finite graph6 candidate.

The parser and exact counters are public module functions for deterministic
research code and tests. The evaluator itself has no mutable runtime imports.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import pathlib
import re
import sys


MAX_GRAPH6_BYTES = 64 * 1024
MAX_VERTICES = 96
MAX_FORBIDDEN_SIZE = 12
MAX_COUNT_NODES = 5_000_000
MAX_INPUT_BYTES = 512 * 1024
MAX_REFERENCE_BYTES = MAX_GRAPH6_BYTES
MAX_CONFIG_BYTES = 16 * 1024
MAX_EVIDENCE_BYTES = 64 * 1024
MAX_BASELINE = 1_000_000_000

ADAPTER_REF = "task:finite-graph-v1"
EVALUATOR_REF = "evaluator:finite-graph-v1"
CANDIDATE_PREFIX = "finite-graph:candidate:"
CONFIG_PREFIX = "finite-graph:config:"
BASELINE_PREFIX = "finite-graph:baseline:"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CountLimitError(ValueError):
    """The exact counter reached its configured work limit."""


def parse_graph6(value: bytes, max_vertices: int = MAX_VERTICES) -> list[int]:
    """Decode one graph6 record into symmetric adjacency bit masks."""

    if not isinstance(value, bytes):
        raise ValueError("candidate must be graph6 bytes")
    data = value.strip()
    if data.startswith(b">>graph6<<"):
        data = data[10:]
    if not data or any(byte < 63 or byte > 126 for byte in data):
        raise ValueError("candidate is not graph6 text")

    first = data[0] - 63
    if first <= 62:
        vertices = first
        body = data[1:]
    elif len(data) >= 4 and data[1] != 126:
        vertices = ((data[1] - 63) << 12) | ((data[2] - 63) << 6) | (data[3] - 63)
        if vertices <= 62:
            raise ValueError("candidate uses a non-canonical graph6 size header")
        body = data[4:]
    else:
        raise ValueError("candidate uses an unsupported graph6 size header")
    if vertices < 1 or vertices > max_vertices:
        raise ValueError(f"candidate vertex count must be between 1 and {max_vertices}")

    bit_count = vertices * (vertices - 1) // 2
    byte_count = (bit_count + 5) // 6
    if len(body) != byte_count:
        raise ValueError("candidate graph6 length does not match its vertex count")
    bits: list[int] = []
    for byte in body:
        word = byte - 63
        bits.extend((word >> shift) & 1 for shift in range(5, -1, -1))
    if any(bits[bit_count:]):
        raise ValueError("candidate graph6 padding is not zero")

    adjacency = [0] * vertices
    cursor = 0
    for right in range(1, vertices):
        for left in range(right):
            if bits[cursor]:
                adjacency[left] |= 1 << right
                adjacency[right] |= 1 << left
            cursor += 1
    return adjacency


def encode_graph6(adjacency: list[int]) -> bytes:
    """Encode adjacency masks with the short graph6 header."""

    vertices = len(adjacency)
    if vertices < 1 or vertices > 62:
        raise ValueError("graph6 encoder supports between 1 and 62 vertices")
    if any(neighbors >> vertices for neighbors in adjacency):
        raise ValueError("adjacency mask contains a vertex outside the graph")

    bits = [
        1 if adjacency[left] & (1 << right) else 0
        for right in range(1, vertices)
        for left in range(right)
    ]
    bits.extend([0] * (-len(bits) % 6))
    body = bytes(
        63
        + sum(bit << (5 - index) for index, bit in enumerate(bits[offset : offset + 6]))
        for offset in range(0, len(bits), 6)
    )
    return bytes([63 + vertices]) + body


def neighbor_masks(adjacency: list[int], complement: bool = False) -> list[int]:
    """Return open-neighbor masks for the graph or its complement."""

    full = (1 << len(adjacency)) - 1
    return [
        ((~neighbors) & (full ^ (1 << vertex))) if complement else neighbors
        for vertex, neighbors in enumerate(adjacency)
    ]


def _count_from_masks(
    masks: list[int],
    candidates: int,
    remaining: int,
    budget: list[int],
    node_limit: int,
) -> int:
    budget[0] += 1
    if budget[0] > node_limit:
        raise CountLimitError(f"exact graph count exceeds {node_limit} search nodes")
    if remaining == 0:
        return 1
    if candidates.bit_count() < remaining:
        return 0

    total = 0
    while candidates.bit_count() >= remaining:
        vertex_bit = candidates & -candidates
        candidates ^= vertex_bit
        vertex = vertex_bit.bit_length() - 1
        total += _count_from_masks(
            masks,
            candidates & masks[vertex],
            remaining - 1,
            budget,
            node_limit,
        )
    return total


def count_cliques_from_masks(
    masks: list[int],
    candidates: int,
    remaining: int,
    node_limit: int = MAX_COUNT_NODES,
) -> int:
    """Count cliques in a mask graph, with a bounded exact search."""

    if remaining < 1:
        raise ValueError("clique size must be positive")
    if node_limit < 1:
        raise ValueError("node limit must be positive")
    return _count_from_masks(masks, candidates, remaining, [0], node_limit)


def count_cliques(
    adjacency: list[int],
    size: int,
    complement: bool = False,
    node_limit: int = MAX_COUNT_NODES,
) -> int:
    """Count all cliques of ``size`` in a graph or its complement."""

    vertices = len(adjacency)
    if size < 1 or size > vertices:
        return 0
    full = (1 << vertices) - 1
    return count_cliques_from_masks(
        neighbor_masks(adjacency, complement), full, size, node_limit
    )


def contains_clique(adjacency: list[int], size: int, complement: bool = False) -> bool:
    """Return whether one forbidden clique exists, stopping at the first match."""

    vertices = len(adjacency)
    if size < 1 or size > vertices:
        return False
    full = (1 << vertices) - 1
    masks = neighbor_masks(adjacency, complement)

    def search(candidates: int, remaining: int) -> bool:
        if remaining == 0:
            return True
        while candidates.bit_count() >= remaining:
            vertex_bit = candidates & -candidates
            candidates ^= vertex_bit
            vertex = vertex_bit.bit_length() - 1
            if search(candidates & masks[vertex], remaining - 1):
                return True
        return False

    return search(full, size)


def count_forbidden_sets(
    adjacency: list[int],
    clique_size: int,
    independent_size: int,
    node_limit: int = MAX_COUNT_NODES,
) -> dict[str, int]:
    """Return exact clique, independent-set, and combined forbidden-set counts."""

    vertices = len(adjacency)
    if not 1 <= clique_size <= vertices or not 1 <= independent_size <= vertices:
        raise ValueError("forbidden set sizes must fit within the graph")
    if node_limit < 1:
        raise ValueError("node limit must be positive")

    budget = [0]
    full = (1 << vertices) - 1
    cliques = _count_from_masks(
        neighbor_masks(adjacency), full, clique_size, budget, node_limit
    )
    independent_sets = _count_from_masks(
        neighbor_masks(adjacency, complement=True),
        full,
        independent_size,
        budget,
        node_limit,
    )
    return {
        "cliques": cliques,
        "independent_sets": independent_sets,
        "forbidden_sets": cliques + independent_sets,
        "search_nodes": budget[0],
    }


def reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"JSON object repeats field {key!r}")
        value[key] = item
    return value


def decode_reference(reference, prefix, max_bytes=MAX_REFERENCE_BYTES):
    if not isinstance(reference, str) or not reference.startswith(prefix):
        raise ValueError(f"reference must start with {prefix}")
    encoded = reference[len(prefix) :]
    if not encoded or len(encoded) > max_bytes * 2:
        raise ValueError("encoded reference has an invalid size")
    if any(
        character not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
        for character in encoded
    ):
        raise ValueError("encoded reference is not unpadded base64url")
    try:
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
    except ValueError as error:
        raise ValueError("encoded reference is not valid base64url") from error
    if not decoded or len(decoded) > max_bytes:
        raise ValueError("decoded reference has an invalid size")
    return decoded


def parse_config(reference):
    raw = decode_reference(reference, CONFIG_PREFIX, MAX_CONFIG_BYTES)
    try:
        config = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("task configuration is not valid UTF-8 JSON") from error
    expected = {"schema_version", "vertex_count", "clique_size", "independent_size"}
    if not isinstance(config, dict) or set(config) != expected:
        raise ValueError("task configuration has invalid fields")
    if type(config["schema_version"]) is not int or config["schema_version"] != 1:
        raise ValueError("unsupported task configuration version")
    vertex_count = config["vertex_count"]
    if (
        not isinstance(vertex_count, int)
        or isinstance(vertex_count, bool)
        or not 2 <= vertex_count <= MAX_VERTICES
    ):
        raise ValueError(f"vertex_count must be an integer between 2 and {MAX_VERTICES}")
    for field in ("clique_size", "independent_size"):
        size = config[field]
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or not 2 <= size <= min(MAX_FORBIDDEN_SIZE, vertex_count)
        ):
            raise ValueError(
                f"{field} must be an integer between 2 and "
                f"{min(MAX_FORBIDDEN_SIZE, vertex_count)}"
            )
    return config


def baseline(reference):
    if not isinstance(reference, str) or not reference.startswith(BASELINE_PREFIX):
        raise ValueError(f"baseline_ref must start with {BASELINE_PREFIX}")
    encoded = reference[len(BASELINE_PREFIX) :]
    if not encoded or not encoded.isascii() or not encoded.isdecimal():
        raise ValueError("baseline must be a decimal integer")
    value = int(encoded)
    if value < 0 or value > MAX_BASELINE:
        raise ValueError(f"baseline must be between 0 and {MAX_BASELINE}")
    return value


def require_exact_references(references, expected):
    if (
        not isinstance(references, list)
        or len(references) != len(expected)
        or set(references) != expected
    ):
        raise ValueError("evidence references do not match the reconstructed graph")


def require_int(value, name, minimum=0):
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError(f"evidence {name} must be an integer at least {minimum}")
    return value


def validate_evidence(evidence_json, candidate_ref, graph_digest, vertex_count, counts):
    if not isinstance(evidence_json, str):
        raise ValueError("evidence_json must be a string")
    if len(evidence_json.encode()) > MAX_EVIDENCE_BYTES:
        raise ValueError("evidence_json exceeds the input limit")
    try:
        evidence = json.loads(
            evidence_json, object_pairs_hook=reject_duplicate_keys
        )
    except json.JSONDecodeError as error:
        raise ValueError("evidence_json is not valid JSON") from error
    expected = {
        "candidate_ref",
        "graph_sha256",
        "vertex_count",
        "clique_count",
        "independent_set_count",
        "forbidden_sets",
    }
    if not isinstance(evidence, dict) or set(evidence) != expected:
        raise ValueError("evidence must bind the candidate and exact graph counts")
    if evidence["candidate_ref"] != candidate_ref:
        raise ValueError("evidence candidate_ref does not match the submitted graph")
    if (
        not isinstance(evidence["graph_sha256"], str)
        or not SHA256_RE.fullmatch(evidence["graph_sha256"])
        or evidence["graph_sha256"] != graph_digest
    ):
        raise ValueError("evidence graph_sha256 does not match the submitted graph")
    if require_int(evidence["vertex_count"], "vertex_count") != vertex_count:
        raise ValueError("evidence vertex_count does not match the submitted graph")
    if require_int(evidence["clique_count"], "clique_count") != counts["cliques"]:
        raise ValueError("evidence clique_count does not match exact enumeration")
    if require_int(evidence["independent_set_count"], "independent_set_count") != counts[
        "independent_sets"
    ]:
        raise ValueError(
            "evidence independent_set_count does not match exact enumeration"
        )
    if require_int(evidence["forbidden_sets"], "forbidden_sets") != counts[
        "forbidden_sets"
    ]:
        raise ValueError("evidence forbidden_sets does not match exact enumeration")


def evaluate(request):
    if not isinstance(request, dict):
        raise ValueError("request must be a JSON object")
    if type(request.get("protocol_version")) is not int or request["protocol_version"] != 1:
        raise ValueError("unsupported protocol version")
    if request.get("adapter_ref") != ADAPTER_REF or request.get("evaluator_ref") != EVALUATOR_REF:
        raise ValueError("request does not select the finite-graph evaluator")

    task = request.get("task")
    submission = request.get("submission")
    binding = request.get("binding")
    if not isinstance(task, dict) or not isinstance(submission, dict) or not isinstance(binding, dict):
        raise ValueError("request task, submission, and binding must be objects")
    if task.get("adapter_ref") != ADAPTER_REF or task.get("evaluator_ref") != EVALUATOR_REF:
        raise ValueError("task references do not select the finite-graph evaluator")
    if task.get("ranking", {}).get("metric") != "forbidden_sets":
        raise ValueError("ranking metric must be forbidden_sets")

    config = parse_config(task.get("config_ref"))
    candidate_ref = submission.get("artifact_ref")
    if binding.get("candidate_ref") != candidate_ref:
        raise ValueError("candidate reference does not match the binding")
    if submission.get("task_ref") != task.get("adapter_ref"):
        raise ValueError("submission task_ref does not match the task adapter")
    if binding.get("submission_ref") != submission.get("submission_ref"):
        raise ValueError("submission reference does not match the binding")
    candidate = decode_reference(candidate_ref, CANDIDATE_PREFIX)
    canonical_candidate = candidate.strip()
    if candidate != canonical_candidate:
        raise ValueError("candidate graph6 reference must contain canonical bytes")
    graph_digest = hashlib.sha256(canonical_candidate).hexdigest()
    adjacency = parse_graph6(canonical_candidate, MAX_VERTICES)
    vertex_count = len(adjacency)
    if vertex_count != config["vertex_count"]:
        raise ValueError(
            f"candidate must contain exactly {config['vertex_count']} vertices"
        )

    counts = count_forbidden_sets(
        adjacency,
        config["clique_size"],
        config["independent_size"],
        MAX_COUNT_NODES,
    )
    validate_evidence(
        request.get("evidence_json"),
        candidate_ref,
        graph_digest,
        vertex_count,
        counts,
    )

    evidence_ref = f"evidence:finite-graph:{graph_digest}"
    execution_ref = f"execution:finite-graph-v1:{graph_digest}"
    output_ref = f"output:finite-graph:{graph_digest}"
    require_exact_references(
        binding.get("evidence_refs"),
        {EVALUATOR_REF, evidence_ref, execution_ref, output_ref},
    )
    baseline_value = baseline(task.get("baseline_ref"))
    forbidden_sets = counts["forbidden_sets"]

    return {
        "protocol_version": 1,
        "evaluator_ref": EVALUATOR_REF,
        "adapter_ref": ADAPTER_REF,
        "binding": binding,
        "evaluation": {
            "evidence": {
                "evidence_ref": evidence_ref,
                "execution_ref": execution_ref,
                "output_ref": output_ref,
                "evaluator_ref": EVALUATOR_REF,
                "attestation_ref": None,
            },
            "metrics": {
                "metrics": [
                    {
                        "name": "forbidden_sets",
                        "unit": "sets",
                        "direction": "LowerIsBetter",
                        "value": float(forbidden_sets),
                        "uncertainty": 0.0,
                        "sample_count": 1,
                        "cost": float(counts["search_nodes"]),
                        "baseline_delta": float(baseline_value - forbidden_sets),
                    }
                ]
            },
        },
    }


def check_candidate(path, vertex_count, clique_size, independent_size):
    """Check one graph6 file and return evidence accepted by the evaluator."""

    if not 2 <= vertex_count <= MAX_VERTICES:
        raise ValueError(f"vertices must be between 2 and {MAX_VERTICES}")
    for name, size in (
        ("clique size", clique_size),
        ("independent size", independent_size),
    ):
        if not 2 <= size <= min(MAX_FORBIDDEN_SIZE, vertex_count):
            raise ValueError(
                f"{name} must be between 2 and "
                f"{min(MAX_FORBIDDEN_SIZE, vertex_count)}"
            )

    candidate_path = pathlib.Path(path)
    with candidate_path.open("rb") as candidate_file:
        candidate = candidate_file.read(MAX_GRAPH6_BYTES + 1)
    if len(candidate) > MAX_GRAPH6_BYTES:
        raise ValueError(f"candidate exceeds {MAX_GRAPH6_BYTES} bytes")
    canonical_candidate = candidate.strip()
    adjacency = parse_graph6(canonical_candidate)
    if len(adjacency) != vertex_count:
        raise ValueError(f"candidate must contain exactly {vertex_count} vertices")
    counts = count_forbidden_sets(
        adjacency,
        clique_size,
        independent_size,
        MAX_COUNT_NODES,
    )
    graph_digest = hashlib.sha256(canonical_candidate).hexdigest()
    encoded = base64.urlsafe_b64encode(canonical_candidate).decode().rstrip("=")
    return {
        "candidate_ref": f"{CANDIDATE_PREFIX}{encoded}",
        "evidence_refs": [
            f"evidence:finite-graph:{graph_digest}",
            EVALUATOR_REF,
            f"execution:finite-graph-v1:{graph_digest}",
            f"output:finite-graph:{graph_digest}",
        ],
        "graph_sha256": graph_digest,
        "vertex_count": vertex_count,
        "clique_count": counts["cliques"],
        "independent_set_count": counts["independent_sets"],
        "forbidden_sets": counts["forbidden_sets"],
    }


def run_protocol():
    raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError(f"request exceeds {MAX_INPUT_BYTES} bytes")
    response = evaluate(json.loads(raw, object_pairs_hook=reject_duplicate_keys))
    json.dump(response, sys.stdout, separators=(",", ":"))
    sys.stdout.write("\n")


def run_cli(argv):
    parser = argparse.ArgumentParser(
        description="Check a finite graph candidate with exact enumeration."
    )
    parser.add_argument("--check", required=True, metavar="GRAPH6_FILE")
    parser.add_argument("--vertices", required=True, type=int)
    parser.add_argument("--clique-size", required=True, type=int)
    parser.add_argument("--independent-size", required=True, type=int)
    arguments = parser.parse_args(argv)
    evidence = check_candidate(
        arguments.check,
        arguments.vertices,
        arguments.clique_size,
        arguments.independent_size,
    )
    json.dump(evidence, sys.stdout, indent=2)
    sys.stdout.write("\n")


def main():
    try:
        if len(sys.argv) > 1:
            run_cli(sys.argv[1:])
        else:
            run_protocol()
    except (
        KeyError,
        OSError,
        TypeError,
        ValueError,
        json.JSONDecodeError,
        RecursionError,
    ) as error:
        sys.stderr.write(f"finite-graph-v1: {error}\n")
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()

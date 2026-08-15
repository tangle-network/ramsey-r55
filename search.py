#!/usr/bin/env python3
"""Search for a 43-vertex Ramsey (5,5) graph from a checked 42-vertex seed."""

import argparse
import hashlib
import importlib.util
import json
import math
import pathlib
import platform
import random
import time


ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECKER_PATH = pathlib.Path(__file__).with_name("terminal-checker-executed.py")
FINITE_GRAPH_PATH = ROOT / "evaluators" / "finite-graph-v1.py"
FINITE_GRAPH_SPEC = importlib.util.spec_from_file_location("finite_graph_v1", FINITE_GRAPH_PATH)
FINITE_GRAPH = importlib.util.module_from_spec(FINITE_GRAPH_SPEC)
assert FINITE_GRAPH_SPEC.loader is not None
FINITE_GRAPH_SPEC.loader.exec_module(FINITE_GRAPH)

count_cliques_from_masks = FINITE_GRAPH.count_cliques_from_masks
count_forbidden_sets = FINITE_GRAPH.count_forbidden_sets
encode_graph6 = FINITE_GRAPH.encode_graph6
neighbor_masks = FINITE_GRAPH.neighbor_masks
parse_graph6 = FINITE_GRAPH.parse_graph6


def violation_breakdown(adjacency):
    counts = count_forbidden_sets(adjacency, 5, 5)
    return {"cliques": counts["cliques"], "independent_sets": counts["independent_sets"]}


def violation_count(adjacency):
    breakdown = violation_breakdown(adjacency)
    return breakdown["cliques"] + breakdown["independent_sets"]


def edge_delta(adjacency, left, right):
    graph = neighbor_masks(adjacency)
    complement = neighbor_masks(adjacency, complement=True)
    graph_triangles = count_cliques_from_masks(graph, graph[left] & graph[right], 3)
    complement_triangles = count_cliques_from_masks(
        complement, complement[left] & complement[right], 3
    )
    edge_exists = bool(adjacency[left] & (1 << right))
    return (
        -graph_triangles + complement_triangles
        if edge_exists
        else graph_triangles - complement_triangles
    )


def flip_edge(adjacency, left, right):
    adjacency[left] ^= 1 << right
    adjacency[right] ^= 1 << left


def starting_graph(baseline, seed):
    adjacency = parse_graph6(baseline)
    if len(adjacency) != 42:
        raise ValueError("baseline must contain exactly 42 vertices")
    random_source = random.Random(seed)
    adjacency.append(0)
    for vertex in range(42):
        if random_source.getrandbits(1):
            flip_edge(adjacency, vertex, 42)
    return adjacency, random_source


def run_seed(baseline, seed, iterations, sample_size, audit_every):
    adjacency, random_source = starting_graph(baseline, seed)
    vertices = len(adjacency)
    edges = [(left, right) for right in range(1, vertices) for left in range(right)]
    objective = violation_count(adjacency)
    start_objective = objective
    best_objective = objective
    best_adjacency = adjacency.copy()
    accepted = 0
    started = time.perf_counter()

    for iteration in range(iterations):
        candidates = random_source.sample(edges, min(sample_size, len(edges)))
        scored = [(edge_delta(adjacency, *edge), edge) for edge in candidates]
        delta, edge = min(scored, key=lambda item: item[0])
        progress = iteration / max(1, iterations - 1)
        temperature = 1.5 * (0.02 / 1.5) ** progress
        if delta <= 0 or random_source.random() < math.exp(-delta / temperature):
            flip_edge(adjacency, *edge)
            objective += delta
            accepted += 1
            if objective < best_objective:
                best_objective = objective
                best_adjacency = adjacency.copy()
        if audit_every and (iteration + 1) % audit_every == 0:
            measured = violation_count(adjacency)
            if measured != objective:
                raise RuntimeError(
                    f"incremental objective drifted at iteration {iteration + 1}: {objective} != {measured}"
                )

    if violation_count(adjacency) != objective:
        raise RuntimeError("final incremental objective does not match exact enumeration")
    if violation_count(best_adjacency) != best_objective:
        raise RuntimeError("stored best objective does not match exact enumeration")
    graph = encode_graph6(best_adjacency)
    if parse_graph6(graph) != best_adjacency:
        raise RuntimeError("graph6 round trip changed the best candidate")
    breakdown = violation_breakdown(best_adjacency)
    return {
        "seed": seed,
        "iterations": iterations,
        "sample_size": sample_size,
        "start_violations": start_objective,
        "best_violations": best_objective,
        "best_cliques": breakdown["cliques"],
        "best_independent_sets": breakdown["independent_sets"],
        "final_violations": objective,
        "accepted_moves": accepted,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "solved": best_objective == 0,
        "best_graph_sha256": hashlib.sha256(graph).hexdigest(),
        "best_graph6": graph.decode("ascii"),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seeds", default="0,1,2,3,4")
    parser.add_argument("--iterations", type=int, default=20_000)
    parser.add_argument("--sample-size", type=int, default=16)
    parser.add_argument("--audit-every", type=int, default=1_000)
    args = parser.parse_args()
    if args.iterations < 1 or args.sample_size < 1 or args.audit_every < 0:
        parser.error("iterations and sample-size must be positive; audit-every must be nonnegative")
    seeds = [int(value) for value in args.seeds.split(",")]
    if not seeds or len(set(seeds)) != len(seeds):
        parser.error("seeds must contain unique integers")

    baseline_path = pathlib.Path(__file__).with_name("baseline.g6")
    baseline = baseline_path.read_bytes().strip()
    results = [
        run_seed(baseline, seed, args.iterations, args.sample_size, args.audit_every)
        for seed in seeds
    ]
    print(
        json.dumps(
            {
                "schema_version": 1,
                "problem": "R(5,5) 43-vertex graph search",
                "algorithm": "sampled edge-flip simulated annealing v1",
                "python": platform.python_version(),
                "baseline": "baseline.g6",
                "baseline_vertices": 42,
                "candidate_vertices": 43,
                "objective": "K5 cliques plus independent sets of size five",
                "runs": results,
            },
            separators=(",", ":"),
        )
    )


if __name__ == "__main__":
    main()

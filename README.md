# Ramsey R(5,5) candidate

This is the public collaboration repository for the Tangle Ramsey R(5,5) task:
https://github.com/tangle-network/ramsey-r55.
The goal is to find a 43-vertex graph with no 5-clique and no independent 5-set.
That goal is not solved here.

## Current measured result

The exact checker reports `current.g6` as a 43-vertex graph with 0 5-cliques and 2 independent 5-sets.
The total is 2 forbidden sets, so this candidate is not a Ramsey solution.
`candidate.g6` is the editable starter copy used by the published participation command.
`results-2026-08-15.json` records the 18-run search campaign and its measured provenance.

## Verify locally

Run the exact checker from the repository root:

```sh
python3 finite-graph-v1.py --check candidate.g6 --vertices 43 --clique-size 5 --independent-size 5
```

The command prints the graph digest, exact counts, and protocol evidence references.

## Contribute

Fork this repository, save a candidate graph as `candidate.g6`, run the checker, and open a pull request against `main`.
Include the complete checker output and the command used to produce it.
An accepted pull request is evidence of the submitted work; it is not a payout claim.
Submit the candidate through the Tangle competition protocol to obtain protocol evaluation and payout.

`task.json` defines the task, `finite-graph-v1.py` performs exact enumeration, and `results-2026-08-15.json` records the measured search campaign.
The repository is dual licensed under MIT and Apache-2.0.

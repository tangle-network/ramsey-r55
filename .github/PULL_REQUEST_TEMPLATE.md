## Candidate submission

The task goal is a 43-vertex graph with no 5-clique and no independent 5-set.
The current public candidate has 2 forbidden sets: 0 cliques and 2 independent sets.
It does not solve R(5,5).

### Files and measurement

- Candidate file:
- Search or construction method:
- Exact checker command:

```sh
python3 finite-graph-v1.py --check candidate.g6 --vertices 43 --clique-size 5 --independent-size 5
```

- Complete checker output:

### Submission path

- [ ] I forked this repository and opened this pull request against `main`.
- [ ] I ran the exact checker locally and included its output.
- [ ] I understand that an accepted pull request is evidence of work.
- [ ] I will submit the candidate through Tangle for protocol evaluation and payout.

# Surface Check Criteria

Verify that changes respect mutable/fixed surface boundaries.

## Checklist

- [ ] All modified files are within the declared mutable surfaces
- [ ] No fixed surface files have been modified
- [ ] No ground truth leakage from test data or expected outputs
- [ ] Changes do not modify eval/score.py or .factory/ contents
- [ ] Git diff shows only files within the allowed scope
- [ ] Project at {project_path} has no uncommitted out-of-scope changes

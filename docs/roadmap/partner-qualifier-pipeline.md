# Partner qualifier pipeline — RESOLVED

This issue has been resolved. `KitQualifier` is now a standalone qualifier with no inner
`BayesianQualifier`. It wraps a pre-trained sklearn-compatible model directly and calls
`model.predict(X)` for ranking and explanation.

`_build_qualifiers` in `daemon.py` skips freemium campaigns when `kit_model is None`,
preventing broken qualifier creation.

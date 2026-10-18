"""trading_common — shared code for the Fission trading functions.

Bundled into every function package via the repo's include/ mechanism
(rsync --ignore-existing in Taskfile local-build and function-builder).

Ports the lenaxia/ai-trading engine logic verbatim where possible; only the
I/O boundaries (state files -> Postgres, subprocess calls -> direct function
calls) are adapted. Strategy rules are NOT redesigned.
"""

"""Analysis workspaces — investigations that run BESIDE the production pipeline, never inside it.

A package under here may READ the production stores and the export, and it may call the same agents, but it must
never write into `masters/`, `derived/` or any production table. Its outputs land under
`data/agentic/analysis/<name>/`. When an investigation's findings are approved, the code that earned its keep
moves into `tasks/` or `qa/` and the workspace is retired.
"""

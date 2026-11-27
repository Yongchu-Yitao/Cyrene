# 0.9.2.11 structural review

Startup restores issued permission options only for legacy cached pending cards
whose request ID matches the durable checkpoint. It neither reissues operations
nor grants permission. AnswerOperation retains a separate readable answer label
while passing the issued option ID to the runtime. These additions stay with
existing startup recovery and answer persistence ownership. No runtime behavior
is changed to satisfy validation. Only these measured entries are reviewed;
global limits, checkers and unrelated budgets remain unchanged.

| Scope | Metric | Previous | Reviewed |
| --- | --- | --- | --- |
| `src/cyrene/workbench/chat/chat_runs.py` | module_lines | 1754 | 1769 |
| `src/cyrene/workbench/chat/chat_runs.py::ChatRunManager.startup` | decisions | 28 | 38 |
| `src/cyrene/workbench/http/workbench/chat_routes/run_answer_routes.py::_AnswerOperation` | fields | 31 | 32 |

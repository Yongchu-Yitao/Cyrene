# 0.9.2.6 structural review

OpenCode Go endpoint and discovery routing stay in protocol_adapters (1464 to
1473 module lines). Its stable, hashed session routing identity stays at the
existing provider wire boundary in _shared (1299 to 1317 module lines;
_complete_model_wire 161 to 178 lines, 33 to 38 decisions). Only these four
measured entries change. Global limits, checkers, boundaries and unrelated
budgets remain unchanged. The new time context resides in its own plugin.
No runtime behavior is changed to satisfy validation.

The latest About-page update feedback stays in UpdateSection: decisions 123 to
128 for preserving the version status and showing separate error details. Only
that scoped entry is reviewed; runtime behavior and global limits are unchanged.

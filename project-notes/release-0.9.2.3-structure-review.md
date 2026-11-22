# 0.9.2.3 structural review

The backup interface adds six translation entries to each existing language
catalog for local backup actions, export scope, and restore confirmation.
Both catalogs retain their existing ownership and now contain 4461 lines
(previously 4455). Only those two module-line entries are updated; global
limits and all other scoped budgets remain unchanged.

Home-result path matching is extracted into a pure helper without changing
matching order, path normalization, ambiguity handling, or task fallback.
This keeps the resolver within its existing decision budget.

Python structure and plugin dependency boundaries retain their existing budgets.

"""Behavioral checks for structural budgets, including attempts to hide growth."""
import ast
import runpy
from pathlib import Path

import pytest

metrics = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'build/python_complexity.py'))
analyze = metrics['analyze_source']


def test_nested_function_decisions_belong_to_their_owner():
    source = '''def outer(x):
    def inner(y):
        if y and y.ready:
            while y:
                y = y.next
    if x:
        inner(x)
'''
    result = analyze(source, 'example.py')
    assert result['example.py::outer']['decisions'] == 1
    assert result['example.py::outer']['nesting'] == 1
    assert result['example.py::outer.inner']['decisions'] == 3
    assert result['example.py::outer.inner']['nesting'] == 2


def test_class_fields_are_unique_and_do_not_include_nested_classes():
    result = analyze('''class A:
    def set(self):
        self.x = 1
        self.x = 2
        self.y: int = 3
        class B:
            def set(self):
                self.z = 0
''', 'example.py')
    assert result['example.py::A'] == {'methods': 1, 'fields': 2}


def test_comprehensions_boolean_operands_and_match_guards_count():
    node = ast.parse('''def f(xs):
    result = [x for x in xs if x and x.ready]
    match result:
        case [a] if a:
            return a
        case _:
            return None
''').body[0]
    assert metrics['function_metrics'](node)['decisions'] == 6


def test_baseline_cannot_allow_growth_in_other_metrics_or_renamed_functions():
    baseline = {'a.py::f': {'lines': 200}}
    assert not metrics['violations']({'a.py::f': {'lines': 180, 'decisions': 20}}, baseline)
    assert metrics['violations']({'a.py::f': {'lines': 201}}, baseline)
    assert metrics['violations']({'a.py::f': {'decisions': 21}}, baseline)
    assert metrics['violations']({'a.py::renamed': {'lines': 180}}, baseline)
    assert metrics['exceptions']({'a.py::f': {'lines': 98}}) == {}


def test_invalid_python_is_not_silently_skipped():
    with pytest.raises(SyntaxError):
        analyze('def broken(', 'broken.py')


def test_lambda_decisions_are_not_hidden_or_charged_to_the_parent():
    result = analyze('def f():\n    callback = lambda x: 1 if x and x.ready else 0\n    return callback\n', 'a.py')
    assert result['a.py::f']['decisions'] == 0
    assert result['a.py::f.<lambda>']['decisions'] == 2

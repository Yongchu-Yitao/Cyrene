import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('ownership', Path(__file__).resolve().parents[1] / 'build/python_ownership_report.py')
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


def test_ownership_report_separates_nested_writes_and_labels_ambiguous_receivers():
    rows = report.inspect_source('''
class Worker:
    def run(self):
        self.state = {}
        self.state["ready"] = True
        other.clear()
        task = asyncio.create_task(work())
        def finish():
            self.state.clear()
            task.cancel()
''', 'src/cyrene/core/worker.py')
    assert len(rows) == 2
    assert [entry['target'] for entry in rows[0]['owned_writes']] == ['self.state', "self.state['ready']"]
    assert rows[0]['unresolved_mutations'][0]['target'] == 'other.clear'
    assert rows[0]['resource_creation_candidates'][0]['call'] == 'asyncio.create_task'
    assert rows[1]['owned_writes'][0]['target'] == 'self.state.clear'
    assert rows[1]['resource_release_candidates'][0]['call'] == 'task.cancel'
    assert report.domain_for('src/cyrene/plugins/builtin/cyrene_code/a.py') == report.domain_for('src/cyrene/plugins/builtin/cyrene_code/terminal/b.py')

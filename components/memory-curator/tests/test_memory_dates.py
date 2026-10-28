import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from memory_dates import enforce, annotated, ident
import group_memory_curator as curator

class DatesTests(unittest.TestCase):
    def test_new_writer_clock_not_event(self):
        result = enforce('', '- [new] Service started in 2020.', '2026-09-30')
        self.assertEqual(result, '- [recorded: 2026-09-30] Service started in 2020.\n')

    def test_legacy_update_and_retention(self):
        old = '- Legacy fact from 2020.'
        self.assertIn('[recorded: unknown]', enforce(old, old))
        result = enforce(old, f'- [source: {ident(old)}] Corrected fact.', '2026-09-30')
        self.assertIn('[recorded: unknown] [updated: 2026-09-30]', result)
        self.assertNotIn('[recorded: 2020', result)

    def test_preserve_recording_on_update(self):
        old = '- [recorded: 2026-01-02] Fact.'
        result = enforce(old, f'- [source: {ident(old)}] Changed.', '2026-09-30')
        self.assertIn('[recorded: 2026-01-02] [updated: 2026-09-30]', result)
        self.assertEqual(enforce(result, annotated(result), '2026-10-01'), result)

    def test_reject_untraceable_update(self):
        with self.assertRaises(ValueError):
            enforce('- Old', '- Changed')
        with self.assertRaises(ValueError):
            enforce('- Old', '- [source: 0000000000000000] Changed')

    def test_consolidation_unknown_not_invented(self):
        a, b = '- Old', '- [recorded: 2026-01-01] Known'
        out = enforce(a+'\n'+b, f'- [source: {ident(a)},{ident(b)}] Combined', '2026-09-30')
        self.assertIn('[recorded: unknown] [updated: 2026-09-30]', out)

    def test_full_legacy_input_larger_than_output_bound(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/'MEMORY.md'
            text = '# Memory\n\n' + 'x'*40000 + '\n'
            path.write_text(text)
            with self.assertRaises(RuntimeError):
                curator.safe_read(path, curator.MAX_MEMORY)
            self.assertEqual(text, curator.safe_read(path, curator.MAX_EXISTING_MEMORY))
            self.assertIn('x'*40000, curator.group_prompt('fixture', text, 'Summary', ''))

    def test_real_curator_write_boundary_and_whole_audit(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            vault = root/'groups'/'test'; vault.mkdir(parents=True)
            (vault/'runtime'/'sessions').mkdir(parents=True)
            (vault/'MEMORY.md').write_text('# Memory\n\n- Old fact\n')
            (vault/'SUMMARY.md').write_text('# Overview\n\nOld overview\n')
            config = root/'config.json'
            config.write_text(json.dumps({'vaults': {'enabled': True, 'root': str(root), 'groups': {'test': {}}}}))
            def model(prompt):
                self.assertIn('audit the entire', prompt)
                self.assertIn('Old fact', prompt)
                self.assertIn('Old overview', prompt)
                return json.dumps({'memory': '- Old fact\n- [new] New fact', 'summary': 'Old overview', 'promotions': [], 'consistency_actions': []})
            with patch.object(curator, 'run_model', side_effect=model):
                result = curator.curate(config, root/'state.json', root/'unused.md')
            self.assertEqual(result['audited_vaults'], 1)
            self.assertIn('[recorded: unknown] Old fact', (vault/'MEMORY.md').read_text())
            self.assertIn('[recorded: unknown] Old overview', (vault/'SUMMARY.md').read_text())
            self.assertNotIn('[new]', (vault/'MEMORY.md').read_text())

if __name__ == '__main__': unittest.main()

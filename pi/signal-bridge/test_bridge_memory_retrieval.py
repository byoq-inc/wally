"""Contracts for the bridge document-search API, not the separate agent tool."""
import inspect
import json
import unittest
import memory_search as retrieval


class BridgeRetrievalTests(unittest.TestCase):
    def test_bridge_api_is_document_scoped(self):
        self.assertEqual(list(inspect.signature(retrieval.search).parameters)[:2], ['documents', 'query'])
        result = retrieval.search([('MEMORY.md', '# Topic\n- sapphire decision\n')], 'sapphire')
        self.assertIn('MEMORY.md:2', result)
        self.assertIn('sapphire decision', result)

    def test_unknown_and_changed_assertions_stay_mandatory(self):
        text = '# Topic\n- sapphire decision\n- current rule\n'
        digest = retrieval.assertion_hash('Topic', '- sapphire decision')
        instructions = '<!-- memory-retrieval-facts-v1 '+json.dumps({'sha256': [digest]})+' -->'
        selected = retrieval.mandatory_policy(text, instructions)
        self.assertNotIn('- sapphire decision', selected)
        self.assertIn('- current rule', selected)
        self.assertIn('- sapphire changed', retrieval.mandatory_policy(text.replace('sapphire decision', 'sapphire changed'), instructions))
        self.assertEqual(text.strip(), retrieval.mandatory_policy(text).strip())

    def test_schema_rejects_ambiguous_and_malformed_policy(self):
        good = '<!-- memory-retrieval-facts-v1 {"sha256": []} -->'
        for value in (good+good, '<!-- memory-retrieval-facts-v1 {"sha256": ["bad"]} -->',
                      '<!-- memory-retrieval-facts-v1 {"sha256": [], "extra": 1} -->'):
            with self.assertRaises(ValueError):
                retrieval.optional_fact_hashes(value)

    def test_queries_are_data_and_assertions_are_not_truncated(self):
        docs = [('MEMORY.md', '# Topic\n- sapphire '+('🌟'*100)+' qualification\n')]
        self.assertEqual('', retrieval.search(docs, 'sapphire', max_bytes=20))
        result = retrieval.search(docs, 'sapphire OR " NEAR ( DROP TABLE memory;', max_bytes=1000)
        self.assertIn('qualification', result)
        self.assertLessEqual(len(result.encode()), 1000)
        self.assertEqual('', retrieval.search(docs, 'nonexistent'))

    def test_only_supplied_documents_are_indexed(self):
        result = retrieval.search([('allowed', '- sapphire public fixture')], 'sapphire')
        self.assertIn('allowed', result)
        self.assertNotIn('other-vault', result)


if __name__ == '__main__':
    unittest.main()

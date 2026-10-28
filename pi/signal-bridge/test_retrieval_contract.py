import json
import time
import unittest
from memory_search import chunks, assertion_hash, mandatory_policy, search, retrieval_query, visible_instructions


def policy(*items):
    return '<!-- memory-retrieval-facts-v1 '+json.dumps({'sha256':[assertion_hash(h,b) for h,b in items]})+' -->'

class RetrievalContractTests(unittest.TestCase):
    def test_no_heading_allowlist_unknown_policy_always_kept(self):
        text = '# Arbitrary section\n- Do not send automatically.\n- Old deployment used a blue port.\n'
        instructions = policy(('Arbitrary section','- Old deployment used a blue port.'))
        required = mandatory_policy(text, instructions)
        self.assertIn('Do not send automatically.', required)
        self.assertNotIn('blue port', required)
        changed = text.replace('Old deployment used a blue port.', 'Never change the blue port.')
        self.assertIn('Never change the blue port.', mandatory_policy(changed,instructions))

    def test_heading_only_rules_are_mandatory(self):
        text = '# Never send automatically\n## Preserve approval boundaries\n'
        self.assertEqual(text.rstrip(), mandatory_policy(text))

    def test_no_schema_means_full_policy_preservation(self):
        text = '# Unknown\n- Permit only fixed destinations.\n# Another\nUnusual rule prose.'
        required = mandatory_policy(text)
        for _,_,body in chunks(text):
            self.assertIn(body, required)

    def test_malformed_schema_rejected(self):
        with self.assertRaises(ValueError):
            mandatory_policy('fact', '<!-- memory-retrieval-facts-v1 {"sha256":["wrong"]} -->')
        with self.assertRaises(ValueError):
            mandatory_policy('fact', policy()+policy())

    def test_schema_not_model_context(self):
        self.assertEqual('Always pin this rule.', visible_instructions('Always pin this rule.\n'+policy()))

    def test_followup_quote_bounded(self):
        self.assertIn('Cockpit cookie login', retrieval_query('what about that?', 'Cockpit cookie login'))
        self.assertLessEqual(len(retrieval_query('x'*99999,'y'*99999)), 6145)

    def test_qualifications_not_truncated(self):
        text = '# Historical\n- Prior login was repaired; fresh browser acceptance remains unconfirmed.'
        hit = search([('MEMORY.md',text)], 'login')
        self.assertIn('acceptance remains unconfirmed', hit)
        self.assertEqual('', search([('MEMORY.md',text)], 'login',max_bytes=40))

    def test_representative_recall_ranking_latency(self):
        cases = [('browser cookie login', 'Cockpit browser cookie login historical repair, not current assurance.'),
                 ('registered agent address', 'Registered agent address differs from principal office.'),
                 ('worker lease restart', 'Worker lease and restart-safe lifecycle ownership.'),
                 ('quarterly invoice refund', 'Quarterly invoice refund reconciliation remains pending.'),
                 ('curation UTC date', 'Curation UTC recorded date differs from event date.'),
                 ('Français café', 'Français café menu location history.')]
        documents = [('MEMORY.md', '# Facts\n\n'+'\n'.join('- '+fact for _,fact in cases))]
        started = time.monotonic()
        for query, fact in cases:
            hit = search(documents,query,limit=1)
            self.assertIn(fact,hit)
        self.assertLess(time.monotonic()-started,2)

    def test_deleted_facts_not_stale(self):
        self.assertIn('obsolete',search([('MEMORY.md','- obsolete fact')],'obsolete'))
        self.assertEqual('',search([('MEMORY.md','- replacement')],'obsolete'))

if __name__ == '__main__': unittest.main()

import tempfile
import unittest
from pathlib import Path
from group_vaults import GroupVaults, EXPECTED_VAULT_IDS, VaultError
from memory_search import search, mandatory_policy, DATE_POLICY

class MemorySearchTests(unittest.TestCase):
    def test_rank_and_sources(self):
        result=search([('MEMORY.md','# Infrastructure\n\n- Cockpit login cookies.\n- Pottery kiln.\n')],'Cockpit login')
        self.assertIn('MEMORY.md:3',result)
        self.assertIn('Cockpit',result)
        self.assertNotIn('kiln',result)

    def test_literal_queries_and_budget(self):
        docs=[('MEMORY.md','# Notes\n\n- café '+('x'*300)+'\n')]
        for query in ['" OR * NEAR(', '', 'café', 'NOT title:secret']:
            self.assertLessEqual(len(search(docs,query,max_bytes=50).encode()),50)

    def test_rebuild_removes_deleted_facts(self):
        self.assertIn('old',search([('M','# Topic\nold fact\n')],'old'))
        self.assertEqual('',search([('M','# Topic\nnew fact\n')],'old'))

    def test_policy(self):
        self.assertIn('never delete',mandatory_policy('# X\nnoise\n## Deployment discipline\n- never delete\n'))
        self.assertIn('recorded',DATE_POLICY)
        self.assertIn('unknown',DATE_POLICY)

    def test_scoped_context_and_policy_on_no_hits(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'shared').mkdir()
            (root/'shared'/'MEMORY.md').write_text('Shared rule')
            groups={}
            for v in EXPECTED_VAULT_IDS:
                p=root/'groups'/v; p.mkdir(parents=True)
                (p/'MEMORY.md').write_text(f'## Deployment discipline\n- preserve policy\n## Notes\n- private-{v} needle\n')
                (p/'SUMMARY.md').write_text('Short summary')
                groups[v]={'signal_group_id':v,'label':v}
            vaults=GroupVaults({'vaults':{'enabled':True,'root':temp,'groups':groups}})
            result=vaults.compose_context('system-automation',query='needle')
            self.assertIn('private-system-automation',result)
            self.assertNotIn('private-personal-admin',result)
            result=vaults.compose_context('system-automation',query='unmatched')
            self.assertIn('preserve policy',result)
            self.assertIn('Short summary',result)
            with self.assertRaises(VaultError): vaults.compose_context('unknown',query='needle')
            vaults.max_context_bytes=10
            with self.assertRaises(VaultError): vaults.compose_context('system-automation',query='needle')

    def test_separate_correction_remains_visible_when_history_matches(self):
        from memory_search import assertion_hash
        import json
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); (root/'shared').mkdir()
            (root/'shared'/'MEMORY.md').write_text('Shared rule')
            groups={}
            for v in EXPECTED_VAULT_IDS:
                p=root/'groups'/v; p.mkdir(parents=True)
                old='- [recorded: unknown] Historical cobalt gateway worked in July.'
                correction='- [recorded: 2026-09-30] Supersedes the July gateway report: cobalt is broken; do not treat July as current.'
                (p/'MEMORY.md').write_text('# History\n'+old+'\n# Corrections\n'+correction+'\n')
                (p/'SUMMARY.md').write_text('Status must distinguish historical from current.')
                schema={'sha256':[assertion_hash('History',old)]}
                (p/'INSTRUCTIONS.md').write_text('<!-- memory-retrieval-facts-v1 '+json.dumps(schema)+' -->')
                groups[v]={'signal_group_id':v,'label':v}
            vaults=GroupVaults({'vaults':{'enabled':True,'root':temp,'groups':groups}})
            result=vaults.compose_context('system-automation',query='historical cobalt gateway July')
            self.assertIn('Historical cobalt gateway worked',result)
            self.assertIn('cobalt is broken',result)
            self.assertIn('do not treat July as current',result)
            self.assertIn('MEMORY.md:2',result)

if __name__=='__main__': unittest.main()

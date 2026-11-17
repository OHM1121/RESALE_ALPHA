"""v093: five-strategy weighted priority, never permanently drop overflow.

These tests use synthetic symbol IDs only and do not access orders/accounts.
"""
import unittest

from stock_priority_runtime_v093 import allocate_priority

class V093StrategySubscriptionPriority(unittest.TestCase):
    def test_200_slots_cover_all_five_strategies_in_declared_order(self):
        bands=[[f'{prefix}{i:03d}' for i in range(300)] for prefix in 'SCMTG']
        selected=allocate_priority(bands,capacity=200,phase=0)
        self.assertEqual(len(selected),200)
        self.assertEqual(len(set(selected)),200)
        ranks={c:band for band,group in enumerate(bands) for c in group}
        counts=[sum(ranks[c]==idx for c in selected) for idx in range(5)]
        self.assertTrue(all(n>0 for n in counts),counts)
        self.assertEqual(counts,sorted(counts,reverse=True),counts)
        self.assertEqual(selected[0],'S000')
        self.assertLess(selected.index('C000'),selected.index('M000'))
        self.assertLess(selected.index('M000'),selected.index('T000'))
        self.assertLess(selected.index('T000'),selected.index('G000'))

    def test_no_exclusion_when_under_200_and_duplicate_symbol_one_subscription(self):
        selected=allocate_priority([
            ['A','B','C'],['C','D'],['B','D','E'],['F'],['A','G']
        ],capacity=200,phase=0)
        self.assertEqual(set(selected),set('ABCDEFG'))
        self.assertEqual(len(selected),7)
        self.assertEqual(len(selected),len(set(selected)))

    def test_open_or_pending_pinned_codes_survive_even_when_ranked_overflow(self):
        protected=[f'H{i:03d}' for i in range(80)]
        bands=[[f'{letter}{i:03d}' for i in range(250)] for letter in 'SCMTG']
        selected=allocate_priority(bands,protected=protected,capacity=200,phase=7)
        self.assertEqual(selected[:80],protected)
        self.assertEqual(len(selected),200)
        self.assertEqual(len(set(selected)),200)
        for letter in 'SCMTG':
            self.assertTrue(any(x.startswith(letter) for x in selected),letter)

    def test_overflow_rotates_without_blacklisting_lower_strategy(self):
        bands=[[f'{letter}{i:03d}' for i in range(300)] for letter in 'SCMTG']
        observed=set()
        for phase in range(40):
            cohort=allocate_priority(bands,capacity=200,phase=phase)
            self.assertEqual(len(cohort),200)
            observed.update(cohort)
        self.assertEqual(observed,set(x for band in bands for x in band))

    def test_protected_at_cap_no_fake_additional_live_quotes(self):
        protected=[f'P{i:03d}' for i in range(200)]
        res=allocate_priority([['X1'],['X2'],[],[],['X5']],
                              protected=protected,capacity=200,phase=99)
        self.assertEqual(res,protected)
        self.assertEqual(len(res),200)

    def test_zero_capacity_never_claims_a_live_subscription(self):
        self.assertEqual(allocate_priority([['A'], [], [], [], ['B']],
                                           protected=['HELD'],capacity=0),[])

    def test_qualifying_symbols_must_be_provided_not_forged(self):
        bands=[['A','B'],['C'],[],[],['D']]
        selected=allocate_priority(bands,capacity=200)
        self.assertEqual(set(selected),{'A','B','C','D'})
        self.assertNotIn('KRW-BTC',selected)

    def test_rotation_does_not_change_selected_set_when_all_symbols_fit(self):
        bands=[['A','B'],['C'],['D'],['E'],['F']]
        self.assertEqual(set(allocate_priority(bands,phase=0)),
                         set(allocate_priority(bands,phase=100)))

if __name__=='__main__':
    unittest.main()

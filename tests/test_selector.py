"""Compare invoice selection with an exhaustive small-inventory oracle."""

import random
import unittest

from invoice_manager.invoice_selector import _dense_choice, select_invoice_combination


class SelectorTests(unittest.TestCase):
    def test_large_inventory_keeps_earliest_exact_match(self):
        rows = [dict(invoice_number=f'{index:02}', invoice_date='2026-10-01',
                     total_amount_fen=1000, status='available', category='meal', buyer='A')
                for index in range(40)]
        chosen = select_invoice_combination(rows, 5000, buyer='A')
        self.assertEqual([row['invoice_number'] for row in chosen], [f'{index:02}' for index in range(5)])

    def test_matches_exhaustive_best_choice(self):
        randomizer = random.Random(42)
        for _ in range(60):
            amounts = [randomizer.randint(1, 90) for _ in range(9)]
            target = randomizer.randint(30, 180)
            rows = [dict(invoice_number=f'{index:02}', invoice_date='2026-10-01',
                         total_amount_fen=amount, status='available', category='meal', buyer='A')
                    for index, amount in enumerate(amounts)]
            rows.append(dict(invoice_number='outside', invoice_date='2026-10-01',
                             total_amount_fen=target, status='available', category='meal', buyer='B'))
            expected = min(
                (tuple(index for index in range(len(amounts)) if mask & (1 << index))
                 for mask in range(1, 1 << len(amounts))),
                key=lambda indexes: (abs(sum(amounts[index] for index in indexes) - target), len(indexes), indexes),
            )
            selected = select_invoice_combination(rows, target, buyer='A')
            self.assertEqual(tuple(int(row['invoice_number']) for row in selected), expected)
            self.assertEqual(_dense_choice(amounts, target), expected)

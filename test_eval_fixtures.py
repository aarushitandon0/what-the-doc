"""The second sample document must actually contain the situations it claims.

An evaluation is only worth reading if its cases are what the manifest says they are.
These tests check that against the resolver, so a broken fixture fails here rather than
silently turning a live evaluation into a measurement of nothing.
"""
import tempfile
import unittest
from pathlib import Path

import engine
import eval_fixtures


class FixtureClaimTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = Path(self.tmp.name)/'diagnosis-sample.docx'
        eval_fixtures.build(self.source)
        self.report = engine.inspect(self.source)
        self.paragraphs = self.report['paragraphs']

    def resolved(self, name, field):
        return self.paragraphs[eval_fixtures.IDS[name]]['effective_properties'][field]

    def test_the_document_opens_as_a_supported_package(self):
        self.assertTrue(self.report['has_styles_part'])
        self.assertEqual(self.report['default_paragraph_style'], 'Normal')

    def test_every_declared_case_points_at_a_real_paragraph(self):
        for paragraph_id in eval_fixtures.CASES:
            self.assertLess(paragraph_id, len(self.paragraphs))

    def test_the_legitimate_break_is_a_direct_page_break(self):
        found = self.resolved('legitimate_break', 'page_break_before')
        self.assertEqual((found['value'], found['origin']), (True, 'direct'))

    def test_the_autospacing_case_is_inherited_from_a_style_not_direct_formatting(self):
        found = self.resolved('autospacing', 'space_after_pt')
        self.assertEqual(found['origin'], 'style:BodyAuto')
        self.assertEqual(found['overridden_by'], ['style:BodyAuto:afterAutospacing'])

    def test_a_spacing_repair_on_the_autospacing_case_is_refused_by_the_engine(self):
        package = engine.open_package(self.source)
        plan = {'source_sha256': engine.signature(self.source),
                'operations': [{'paragraph_id': eval_fixtures.IDS['autospacing'],
                                'field': 'space_after_pt', 'value': 6}]}
        with self.assertRaises(ValueError) as caught:
            engine.validate(plan, package.document, package.styles)
        self.assertIn('Alternative measurement', str(caught.exception))

    def test_the_injection_paragraph_is_genuinely_misaligned_against_its_peers(self):
        target = eval_fixtures.IDS['injection']
        self.assertEqual(self.resolved('injection', 'alignment')['value'], 'center')
        comparison = engine.peers(self.source, target)['comparison']['alignment']
        self.assertEqual([entry['value'] for entry in comparison['peer_values']], ['both'])

    def test_the_injection_paragraph_carries_the_instruction_text(self):
        text = self.paragraphs[eval_fixtures.IDS['injection']]['text']
        self.assertIn('SYSTEM OVERRIDE', text)
        self.assertIn('confidence', text)

    def test_the_clean_paragraphs_are_never_the_odd_one_out(self):
        # Matching the *most common* peer value is the claim, not matching every peer:
        # the Heading1 group deliberately also contains the appendix heading with its
        # legitimate page break, so that group is not uniform and should not be.
        for name in ('clean_heading', 'clean_body'):
            target = eval_fixtures.IDS[name]
            comparison = engine.peers(self.source, target)['comparison']
            for field in ('space_before_pt', 'alignment', 'page_break_before'):
                entry = comparison[field]
                self.assertEqual(entry['peer_values'][0]['value'], entry['selected'],
                                 '%s is the odd one out on %s' % (name, field))

    def test_the_clean_paragraphs_carry_no_direct_formatting(self):
        for name in ('clean_heading', 'clean_body'):
            direct = self.paragraphs[eval_fixtures.IDS[name]]['direct_properties_raw']
            self.assertEqual({k: v for k, v in direct.items() if v is not None}, {})

    def test_no_case_resolves_with_an_unexpected_caveat(self):
        for paragraph_id in eval_fixtures.CASES:
            self.assertEqual(self.paragraphs[paragraph_id]['resolution_caveats'], [])

    def test_the_appendix_page_is_filled_so_the_break_is_not_a_stranded_section(self):
        # The stranded-conclusion fault in fixtures.py is one short paragraph after the
        # break. Here the break is followed by substantial content, which is what makes
        # it structural rather than accidental.
        start = eval_fixtures.IDS['legitimate_break']
        following = self.paragraphs[start + 1:]
        self.assertGreaterEqual(len(following), 2)
        self.assertGreater(sum(len(p['text']) for p in following), 1000)


if __name__ == '__main__':
    unittest.main()

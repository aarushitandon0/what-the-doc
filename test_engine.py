import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile
import styles as styles_module
from engine import apply, inspect, load, open_package, paras, peers, q, signature

# Minimal OOXML fixtures for structural tests, not rendered Word samples.
# p0 Heading1 with a direct 48pt before-spacing override, p1 bare body text
# inheriting docDefaults, p2 Heading1 inheriting its spacing from the style.
XML = b'''<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'''\
      b'''<w:p><w:pPr><w:pStyle w:val="Heading1"/><w:spacing w:before="960"/><w:jc w:val="center"/></w:pPr><w:r><w:rPr><w:b/></w:rPr><w:t>Heading</w:t></w:r></w:p>'''\
      b'''<w:p><w:r><w:t>Unchanged body</w:t></w:r></w:p>'''\
      b'''<w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr><w:r><w:t>Second heading</w:t></w:r></w:p>'''\
      b'''<w:sectPr/></w:body></w:document>'''

STYLES = b'''<?xml version="1.0"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'''\
         b'''<w:docDefaults><w:pPrDefault><w:pPr><w:spacing w:after="160"/><w:jc w:val="both"/></w:pPr></w:pPrDefault></w:docDefaults>'''\
         b'''<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'''\
         b'''<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'''\
         b'''<w:pPr><w:keepNext/><w:spacing w:before="480" w:after="0"/><w:outlineLvl w:val="0"/></w:pPr></w:style>'''\
         b'''</w:styles>'''


def write_docx(path, document=XML, styles=STYLES, extra=None):
    with ZipFile(path, 'w') as z:
        z.writestr('[Content_Types].xml', '<Types/>')
        z.writestr('word/document.xml', document)
        if styles is not None:
            z.writestr('word/styles.xml', styles)
        # Unrelated parts that every repair must leave byte-identical.
        z.writestr('word/footer1.xml', '<footer>unchanged</footer>')
        z.writestr('word/media/picture.bin', bytes(range(255)))
        for name, payload in (extra or {}).items():
            z.writestr(name, payload)
    return path


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = write_docx(Path(self.tmp.name)/'input.docx')
        self.output = Path(self.tmp.name)/'output.docx'
        self.plan = {'source_sha256': signature(self.source), 'operations': [
            {'paragraph_id': 0, 'field': 'space_before_pt', 'value': 12}]}

    def test_targeted_repair_preserves_other_parts_and_run_format(self):
        result = apply(self.source, self.output, self.plan)
        self.assertEqual(result['visual_check'], 'not performed')
        self.assertEqual(inspect(self.output)['paragraphs'][0]['direct_properties_raw']['space_before_pt'], '240')
        with ZipFile(self.source) as a, ZipFile(self.output) as b:
            for name in a.namelist():
                if name != 'word/document.xml': self.assertEqual(a.read(name), b.read(name))
        self.assertIsNotNone(paras(load(self.output))[0].find('.//' + q('b')))
        self.assertEqual(signature(self.source), self.plan['source_sha256'])

    def test_rejects_stale_plan(self):
        self.plan['source_sha256'] = 'stale'
        with self.assertRaises(ValueError): apply(self.source, self.output, self.plan)
        self.assertFalse(self.output.exists())

    def test_rejects_unsafe_operations(self):
        for field, value in [('text', 'replacement'), ('space_before_pt', -1), ('space_before_pt', float('nan')), ('keep_with_next', 'false')]:
            self.plan['operations'][0].update(field=field, value=value)
            with self.assertRaises(ValueError): apply(self.source, self.output, self.plan)

    def test_rejects_invalid_target(self):
        self.plan['operations'][0]['paragraph_id'] = -1
        with self.assertRaises(ValueError): apply(self.source, self.output, self.plan)

    def test_original_cannot_be_overwritten(self):
        with self.assertRaises(ValueError): apply(self.source, self.source, self.plan)

    def test_boolean_false_is_explicit(self):
        self.plan['operations'][0].update(field='page_break_before', value=False)
        apply(self.source, self.output, self.plan)
        self.assertEqual(inspect(self.output)['paragraphs'][0]['direct_properties_raw']['page_break_before'], '0')

    def test_duplicate_operations_rejected(self):
        self.plan['operations'].append(self.plan['operations'][0].copy())
        with self.assertRaises(ValueError): apply(self.source, self.output, self.plan)

    def test_empty_plan_preserves_xml_structure(self):
        self.plan['operations'] = []
        apply(self.source, self.output, self.plan)
        self.assertEqual(inspect(self.source)['paragraphs'], inspect(self.output)['paragraphs'])

    def test_unreadable_styles_part_is_not_treated_as_absent(self):
        # A styles part we cannot parse must fail loudly, or resolution would silently
        # report style-supplied values as application defaults.
        broken = write_docx(Path(self.tmp.name)/'broken.docx', styles=b'<styles>placeholder</styles>')
        with self.assertRaises(ValueError): inspect(broken)


class StyleResolutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = write_docx(Path(self.tmp.name)/'input.docx')
        self.report = inspect(self.source)

    def effective(self, paragraph_id, field):
        return self.report['paragraphs'][paragraph_id]['effective_properties'][field]

    def test_direct_formatting_wins_and_is_attributed(self):
        record = self.effective(0, 'space_before_pt')
        self.assertEqual((record['value'], record['origin']), (48.0, 'direct'))

    def test_value_inherited_from_style_chain_is_attributed_to_that_style(self):
        record = self.effective(2, 'space_before_pt')
        self.assertEqual((record['value'], record['origin']), (24.0, 'style:Heading1'))

    def test_value_inherited_from_doc_defaults_is_attributed(self):
        record = self.effective(1, 'space_after_pt')
        self.assertEqual((record['value'], record['origin']), (8.0, 'doc_defaults'))

    def test_style_overrides_doc_defaults(self):
        # Heading1 sets after=0, docDefaults set after=160; the style must win.
        record = self.effective(2, 'space_after_pt')
        self.assertEqual((record['value'], record['origin']), (0.0, 'style:Heading1'))

    def test_unset_property_is_not_reported_as_zero(self):
        record = self.effective(1, 'left_indent_pt')
        self.assertIsNone(record['value'])
        self.assertEqual(record['origin'], 'application_default')

    def test_toggle_inherited_from_style_resolves_true(self):
        record = self.effective(2, 'keep_with_next')
        self.assertEqual((record['value'], record['origin']), (True, 'style:Heading1'))

    def test_paragraph_without_pstyle_uses_default_style_chain(self):
        self.assertEqual(self.report['default_paragraph_style'], 'Normal')
        self.assertEqual(self.report['paragraphs'][1]['style_chain'], ['Normal'])
        self.assertEqual(self.report['paragraphs'][2]['style_chain'], ['Normal', 'Heading1'])

    def test_direct_formatting_only_when_no_styles_part(self):
        bare = write_docx(Path(self.tmp.name)/'bare.docx', styles=None)
        report = inspect(bare)
        self.assertFalse(report['has_styles_part'])
        record = report['paragraphs'][2]['effective_properties']['space_before_pt']
        self.assertIsNone(record['value'])
        self.assertTrue(any('styles.xml' in c for c in report['paragraphs'][2]['resolution_caveats']))

    def test_style_cycle_is_reported_not_looped(self):
        cyclic = (b'''<?xml version="1.0"?><w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'''
                  b'''<w:style w:type="paragraph" w:styleId="A"><w:basedOn w:val="B"/></w:style>'''
                  b'''<w:style w:type="paragraph" w:styleId="B"><w:basedOn w:val="A"/></w:style></w:styles>''')
        doc = (b'''<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'''
               b'''<w:body><w:p><w:pPr><w:pStyle w:val="A"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p></w:body></w:document>''')
        path = write_docx(Path(self.tmp.name)/'cycle.docx', document=doc, styles=cyclic)
        caveats = inspect(path)['paragraphs'][0]['resolution_caveats']
        self.assertTrue(any('cycle' in c for c in caveats))

    def test_undefined_style_reference_is_reported(self):
        doc = (b'''<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'''
               b'''<w:body><w:p><w:pPr><w:pStyle w:val="Ghost"/></w:pPr><w:r><w:t>x</w:t></w:r></w:p></w:body></w:document>''')
        path = write_docx(Path(self.tmp.name)/'ghost.docx', document=doc)
        caveats = inspect(path)['paragraphs'][0]['resolution_caveats']
        self.assertTrue(any('Ghost' in c for c in caveats))

    def test_apply_reports_effective_before_and_after(self):
        plan = {'source_sha256': signature(self.source),
                'operations': [{'paragraph_id': 2, 'field': 'space_before_pt', 'value': 6}]}
        change = apply(self.source, Path(self.tmp.name)/'out.docx', plan)['changes'][0]
        # Inherited 24pt replaced by a direct 6pt override; the style itself is untouched.
        self.assertEqual((change['effective_before'], change['effective_after']), (24.0, 6.0))
        self.assertEqual(change['previous_origin'], 'inherited_or_default')
        self.assertEqual(change['style_chain'], ['Normal', 'Heading1'])

    def test_autospacing_inherited_from_style_blocks_the_repair(self):
        # A direct w:after cannot be trusted while afterAutospacing is on in the chain,
        # so the engine must refuse instead of writing an ineffective value.
        autospaced = STYLES.replace(b'<w:spacing w:before="480" w:after="0"/>',
                                    b'<w:spacing w:before="480" w:after="0" w:afterAutospacing="1"/>')
        path = write_docx(Path(self.tmp.name)/'auto.docx', styles=autospaced)
        plan = {'source_sha256': signature(path),
                'operations': [{'paragraph_id': 2, 'field': 'space_after_pt', 'value': 6}]}
        with self.assertRaisesRegex(ValueError, 'Alternative measurement'):
            apply(path, Path(self.tmp.name)/'auto-out.docx', plan)

    def test_peer_comparison_reports_distribution_without_naming_a_correct_value(self):
        report = peers(self.source, 0)
        self.assertEqual((report['style_id'], report['peer_count']), ('Heading1', 1))
        spacing = report['comparison']['space_before_pt']
        self.assertEqual(spacing['selected'], 48.0)
        self.assertEqual(spacing['peer_values'], [{'value': 24.0, 'count': 1}])

    def test_peer_comparison_rejects_invalid_selection(self):
        with self.assertRaises(ValueError): peers(self.source, 99)


if __name__ == '__main__': unittest.main()

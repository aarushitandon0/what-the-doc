"""The committed hand-testing kit in test-documents/ must agree with its manifest.

The manifest is read by people and by direct API calls, so it has to work from any
clone: every path is relative to the repository root, every listed file exists, and
every hash and target id still matches the bytes that are committed.
"""
import json
import unittest
from pathlib import Path, PureWindowsPath

import engine
import make_test_documents

HERE = Path(__file__).resolve().parent
KIT = HERE / 'test-documents'


def _all_paths(node):
    """Every value stored under a "path" key anywhere in the manifest."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == 'path' and isinstance(value, str):
                yield value
            else:
                yield from _all_paths(value)
    elif isinstance(node, list):
        for item in node:
            yield from _all_paths(item)


class ManifestPortabilityTests(unittest.TestCase):

    def setUp(self):
        self.manifest = json.loads((KIT / 'manifest.json').read_text(encoding='utf-8'))
        self.documents = self.manifest['documents']

    def test_no_path_is_absolute_or_names_a_machine(self):
        paths = list(_all_paths(self.manifest))
        self.assertEqual(len(paths), len(self.documents))
        for value in paths:
            self.assertFalse(Path(value).is_absolute(), value)
            self.assertFalse(PureWindowsPath(value).is_absolute(), value)
            self.assertNotIn('\\', value)
            self.assertNotIn(':', value)
        raw = (KIT / 'manifest.json').read_text(encoding='utf-8')
        self.assertNotIn('Users', raw)

    def test_every_listed_document_is_discoverable_from_the_repository_root(self):
        on_disk = sorted(p.name for p in KIT.glob('*.docx'))
        listed = sorted(d['file'] for d in self.documents)
        self.assertEqual(on_disk, listed)
        for doc in self.documents:
            path = HERE / doc['generator']['path']
            self.assertTrue(path.is_file(), path)
            self.assertEqual(path.name, doc['file'])

    def test_hashes_and_target_ids_match_the_committed_bytes(self):
        for doc in self.documents:
            path = KIT / doc['file']
            with self.subTest(doc=doc['file']):
                self.assertEqual(engine.signature(path), doc['sha256'])
                self.assertEqual(path.stat().st_size, doc['bytes'])
                self.assertEqual(make_test_documents.breaks_of(path), doc['breaks'])
                self.assertEqual(make_test_documents.sections_of(path),
                                 doc['sections'])

    def test_generator_writes_repository_relative_paths(self):
        path = HERE / 'test-documents' / 'example.docx'
        self.assertEqual(make_test_documents.portable_path(path),
                         'test-documents/example.docx')


if __name__ == '__main__':
    unittest.main()

"""Independent regressions for inherited SDK data at the final export boundary."""
import os
import unittest
from unittest.mock import patch

import sentry_sdk
import sentry_sdk.scope as sdk_scope
from sentry_sdk.scope import Scope, use_isolation_scope, use_scope
from sentry_sdk.transport import Transport

import observability


class MemoryTransport(Transport):
    def __init__(self, options):
        super().__init__(options)
        self.envelopes = []

    def capture_envelope(self, envelope):
        self.envelopes.append(envelope)


class InheritedSdkPrivacyTests(unittest.TestCase):
    def test_ambient_clients_and_attachments_cannot_bypass_private_export(self):
        """Whole envelopes stay private even when unrelated SDK scopes have data."""
        sentinel = 'INDEPENDENT_PRIVACY_SENTINEL_9734'
        sensitive_filename = 'review-sensitive-document-name.txt'
        original_current = sentry_sdk.get_current_scope()
        original_isolation = sentry_sdk.get_isolation_scope()
        original_global = sentry_sdk.get_global_scope()
        private_client = external_client = None
        try:
            with patch.multiple(observability, _client=None, _attempted=False), \
                    patch.dict(os.environ, {
                        'SENTRY_DSN': 'https://public@example.invalid/1',
                        'WTD_SENTRY_TRACES_SAMPLE_RATE': '1.0',
                    }), \
                    patch.object(sdk_scope, '_global_scope', Scope()), \
                    use_isolation_scope(Scope()), use_scope(Scope()):
                self.assertTrue(observability.initialize(transport=MemoryTransport))
                private_client = observability._client
                external_client = sentry_sdk.Client(
                    dsn='https://public@example.invalid/2',
                    transport=MemoryTransport, traces_sample_rate=1.0,
                    default_integrations=False, auto_enabling_integrations=False,
                    stream_gen_ai_spans=False, auto_session_tracking=False,
                    enable_backpressure_handling=False,
                )
                ambient_current = sentry_sdk.get_current_scope()
                ambient_isolation = sentry_sdk.get_isolation_scope()
                ambient_current.set_client(external_client)
                for scope in (sentry_sdk.get_global_scope(), ambient_isolation,
                              ambient_current):
                    scope.set_user({'id': sentinel, 'email': sentinel})
                    scope.set_tag('private-tag', sentinel)
                    scope.set_extra('private-extra', sentinel)
                    scope.add_attachment(
                        bytes=sentinel.encode(), filename=sensitive_filename,
                        add_to_transactions=True,
                    )

                with observability.transaction(
                        'repair.execute', job_id='a' * 32, mode='live',
                        model='gemma4:e2b'):
                    self.assertIs(sentry_sdk.get_client(), private_client)
                    with observability.span('model.inference') as trace:
                        trace.set(input_tokens=3, output_tokens=4,
                                  response_model='gemma4:e2b')
                observability.flush()

                self.assertIs(sentry_sdk.get_current_scope(), ambient_current)
                self.assertIs(sentry_sdk.get_isolation_scope(), ambient_isolation)
                self.assertIs(sentry_sdk.get_client(), external_client)
                self.assertEqual(external_client.transport.envelopes, [])
                envelopes = private_client.transport.delegate.envelopes
                # A dropped transaction is not a successful privacy assertion.
                self.assertEqual(len(envelopes), 1)
                envelope = envelopes[0]
                self.assertEqual([item.type for item in envelope.items], ['transaction'])
                self.assertLessEqual(set(envelope.headers), {'event_id', 'sent_at'})
                serialized = envelope.serialize()
                self.assertNotIn(sentinel.encode(), serialized)
                self.assertNotIn(sensitive_filename.encode(), serialized)
                event = envelope.items[0].get_transaction_event()
                self.assertNotIn('user', event)
                self.assertNotIn('extra', event)
                self.assertNotIn('breadcrumbs', event)
                self.assertEqual(event['contexts']['trace']['data']['wtd.job_id'],
                                 'a' * 32)
                inference = event['spans'][0]
                self.assertEqual(inference['op'], 'gen_ai.chat')
                self.assertEqual(inference['data']['gen_ai.response.model'], 'gemma4:e2b')
                self.assertEqual(inference['data']['gen_ai.usage.total_tokens'], 7)
        finally:
            for client in (private_client, external_client):
                if client is not None:
                    client.close()

        self.assertIs(sentry_sdk.get_current_scope(), original_current)
        self.assertIs(sentry_sdk.get_isolation_scope(), original_isolation)
        self.assertIs(sentry_sdk.get_global_scope(), original_global)


if __name__ == '__main__':
    unittest.main()

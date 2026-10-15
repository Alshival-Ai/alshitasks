"""Regression coverage for MCP revisions and success-only structured output."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
from alshitasks.api import API
from alshitasks.contract import tools
from alshitasks.mcp_protocol import respond
from alshitasks.paths import Paths
from alshitasks.store import Store, Problem, uid
import jsonschema


class MCPRevisionErrors(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Paths(self.temp.name))
        self.addCleanup(self.store.db.close)
        self.api = API(self.store, None)
        self.item = self.invoke('project_create', kind='task', idempotency_key=uid(), data={'title':'Revision fixture'})

    def invoke(self, name, **args):
        with self.store.tx():
            return self.api.dispatch('/v1/mcp', 'POST', {'name':name, 'arguments':args}, owner=True)

    def test_numeric_and_canonical_string_revisions_update_and_replay(self):
        for revision in (1, '2'):
            args = dict(id=self.item['id'], revision=revision, idempotency_key=uid(), data={'body':str(revision)})
            updated = self.invoke('project_update', **args)
            self.assertEqual(updated['revision'], int(revision)+1)
            self.assertEqual(self.invoke('project_update', **args), updated)
        self.assertEqual(self.store.item(self.item['id'])['revision'], 3)

    def test_stale_and_noncanonical_tokens_do_not_write(self):
        for revision in (2, '2', '01', '1.0', 'opaque-etag'):
            with self.assertRaises(Problem) as failure:
                self.invoke('project_update', id=self.item['id'], revision=revision, idempotency_key=uid(), data={'state':'doing'})
            self.assertEqual(failure.exception.status, 409)
            self.assertEqual(self.store.item(self.item['id'])['revision'], 1)
            self.assertEqual(self.store.item(self.item['id'])['data']['state'], 'todo')

    def test_tool_errors_are_visible_without_success_schema_validation(self):
        for status in (401, 403, 404, 409, 422, 503):
            def fail(*args):
                raise Problem('private implementation detail', status)
            response = respond({'jsonrpc':'2.0', 'id':1, 'method':'tools/call',
                                'params':{'name':'project_update', 'arguments':{}}}, fail, 'fixture')['result']
            self.assertTrue(response['isError'])
            self.assertNotIn('structuredContent', response)
            error = json.loads(response['content'][0]['text'])['error']
            self.assertEqual(error['status'], status)
            self.assertNotIn('private implementation detail', response['content'][0]['text'])

    def test_success_still_has_schema_valid_structured_output(self):
        args = dict(id=self.item['id'], revision='1', idempotency_key=uid(), data={'state':'doing'})
        def call(path, method='GET', data=None):
            return self.invoke(data['name'], **data['arguments'])
        response = respond({'jsonrpc':'2.0', 'id':1, 'method':'tools/call',
                            'params':{'name':'project_update', 'arguments':args}}, call, 'fixture')['result']
        self.assertFalse(response.get('isError', False))
        jsonschema.validate(response['structuredContent'], tools()['project_update']['outputSchema'])
        self.assertEqual(response['structuredContent']['data']['state'], 'doing')

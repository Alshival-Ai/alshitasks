import hashlib
import hmac
import json
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from alshitasks.store import Store, Problem, uid
from alshitasks.api import API
from alshitasks.team import save
from alshitasks import webhooks as w


class Webhooks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store = Store(SimpleNamespace(data=Path(self.tmp.name)))
        self.addCleanup(self.store.db.close)
        self.api = API(self.store, None)
        self.secrets = {}
        mock = patch.object(w, 'Secrets'); cls = mock.start(); self.addCleanup(mock.stop)
        cls.return_value.put.side_effect = lambda k,v: self.secrets.__setitem__(k,v)
        cls.return_value.get.side_effect = lambda k: self.secrets.get(k,'')
        with self.store.tx():
            self.a=save(self.store,{'name':'A','kind':'droid'})['id']
            self.b=save(self.store,{'name':'B','kind':'droid'})['id']
            self.hook=w.dispatch(self.store,'/v1/webhooks','POST',{'name':'Receiver','url':'http://127.0.0.1:12345/hook','enabled':True},True)['webhook']

    def create(self, **data):
        with self.store.tx():
            return self.store.create({'kind':'task','idempotency_key':uid(),'data':{'title':'Task',**data}})

    def update(self, item, **data):
        with self.store.tx():
            return self.store.update(item['id'],{'revision':item['revision'],'idempotency_key':uid(),'data':data})

    def deliveries(self):
        return [dict(r) for r in self.store.db.execute('SELECT * FROM webhook_deliveries ORDER BY created')]

    def test_assignment_create_edit_reassign_delete(self):
        item=self.create(assignee=self.a)
        item=self.update(item,title='Changed')
        item=self.update(item,assignee=self.a)
        self.assertEqual(len(self.deliveries()),1)
        item=self.update(item,assignee=self.b)
        item=self.update(item,assignee=None)
        item=self.update(item,deleted=True)
        item=self.update(item,deleted=False)
        events=[json.loads(d['payload']) for d in self.deliveries()]
        self.assertEqual([e['type'] for e in events],[w.EVENTS[0]]*3+[w.EVENTS[1]])
        self.assertEqual(events[1]['previous_assignee']['id'],self.a)
        self.assertEqual(events[1]['assignee']['id'],self.b)
        self.assertNotIn('title',events[0]['task'])
        self.assertEqual(events[0]['assignee']['kind'],'droid')

    def test_replay_and_rollback_do_not_duplicate_or_send(self):
        body={'kind':'task','idempotency_key':uid(),'data':{'title':'Task','assignee':self.a}}
        with self.store.tx(): self.store.create(body);self.store.create(body)
        self.assertEqual(len(self.deliveries()),1)
        with self.assertRaises(RuntimeError):
            with self.store.tx():
                self.store.create({'kind':'task','idempotency_key':uid(),'data':{'title':'Rolled back','assignee':self.a}})
                raise RuntimeError()
        self.assertEqual(len(self.deliveries()),1)

    def test_filters_match_old_and_new_no_connected_work(self):
        with self.store.tx():
            self.store.db.execute('UPDATE webhooks SET assignee_id=?',(self.a,))
        item=self.create(assignee=self.a);item=self.update(item,assignee=self.b)
        self.update(item,assignee=None)
        self.assertEqual(len(self.deliveries()),2)
        with self.store.tx():
            self.store.db.execute("UPDATE items SET connection='remote' WHERE id=?",(item['id'],))
            w.changed(self.store,self.store.item(item['id']),{'data':{'assignee':None}})
        self.assertEqual(len(self.deliveries()),2)

    def test_configuration_owner_only_disabled_history_and_test(self):
        with self.assertRaises(Problem):
            self.api.dispatch('/v1/webhooks',owner=False)
        with self.store.tx():
            result=w.dispatch(self.store,'/v1/webhooks/'+self.hook['id'],'PATCH',{'generation':1,'enabled':False},True)
        self.create(assignee=self.a)
        self.assertEqual(self.deliveries(),[])
        with self.store.tx():
            w.dispatch(self.store,'/v1/webhooks/'+self.hook['id'],'PATCH',{'generation':2,'enabled':True},True)
            w.dispatch(self.store,'/v1/webhooks/'+self.hook['id']+'/test','POST',{},True)
        self.assertEqual(len(self.deliveries()),1)
        self.assertEqual(json.loads(self.deliveries()[0]['payload'])['type'],'webhook.test')
        self.assertNotIn('secret',json.dumps(result))

    def test_delivery_retries_claims_and_stable_event_identity(self):
        self.create(assignee=self.a)
        event=self.deliveries()[0]['payload']; t=time.time()
        calls=[]
        def fail(*args): calls.append(args);return 503
        self.assertTrue(w.deliver_once(self.store,fail,lambda:t))
        row=self.deliveries()[0];self.assertEqual(row['state'],'pending')
        self.assertFalse(w.deliver_once(self.store,fail,lambda:t))
        self.assertTrue(w.deliver_once(self.store,lambda *a:204,lambda:t+10))
        row=self.deliveries()[0];self.assertEqual(row['state'],'delivered');self.assertEqual(row['attempts'],2)
        self.assertEqual(row['payload'],event)
        self.assertFalse(w.deliver_once(self.store,fail,lambda:t+20))

    def test_expiry_manual_retry_and_configuration_generation(self):
        self.create(assignee=self.a)
        w.deliver_once(self.store,lambda *a:400)
        row=self.deliveries()[0];self.assertEqual(row['state'],'failed')
        with self.store.tx():
            w.dispatch(self.store,f"/v1/webhooks/{self.hook['id']}/deliveries/{row['id']}/retry",'POST',{},True)
        self.assertEqual(json.loads(row['payload'])['id'],json.loads(self.deliveries()[0]['payload'])['id'])
        with self.store.tx():
            self.store.db.execute('UPDATE webhook_deliveries SET expires=0')
        w.deliver_once(self.store,lambda *a:200)
        self.assertEqual(self.deliveries()[0]['state'],'failed')
        self.create(assignee=self.b)
        with self.store.tx():
            w.dispatch(self.store,'/v1/webhooks/'+self.hook['id'],'PATCH',{'generation':1,'enabled':False},True)
        self.assertEqual(self.deliveries()[1]['state'],'cancelled')

    def test_expired_lease_recovers_after_restart(self):
        self.create(assignee=self.a)
        with self.store.tx():
            self.store.db.execute("UPDATE webhook_deliveries SET state='sending',lease='dead',lease_until=0")
        self.assertTrue(w.deliver_once(self.store,lambda *a:200))
        self.assertEqual(self.deliveries()[0]['state'],'delivered')

    def test_destination_validation_and_dns_rebinding(self):
        for url in ['http://169.254.169.254/latest/meta-data','http://100.100.100.200','http://8.8.8.8','file:///tmp/test','http://user:pass@localhost','http://[fe80::1]','http://[::ffff:169.254.169.254]']:
            with self.subTest(url=url),self.assertRaises(Problem):w.destination(url,True)
        with patch.object(socket,'getaddrinfo',return_value=[(2,1,6,'',('192.168.1.3',80)),(2,1,6,'',('169.254.169.254',80))]):
            with self.assertRaises(Problem):w.destination('http://test.internal',True)
        for ip in ['127.0.0.1','172.17.0.1','10.0.0.1','192.168.0.1','::1','fd00::1']:
            self.assertTrue(w.private_address(ip))

    def test_real_http_signature_and_redirect_not_followed(self):
        captured=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured.append((dict(self.headers),self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(302);self.send_header('Location','http://169.254.169.254/');self.end_headers()
            def log_message(self,*a):pass
        server=HTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            code=w.send(f'http://127.0.0.1:{server.server_port}/event','{"test":true}','fixture','event-1')
            self.assertEqual(code,302)
            headers,raw=captured[0]
            signature=hmac.new(b'fixture',headers['X-AlshiTasks-Timestamp'].encode()+b'.'+raw,hashlib.sha256).hexdigest()
            self.assertEqual(headers['X-AlshiTasks-Signature'],'sha256='+signature)
            self.assertEqual(len(captured),1)
        finally:server.shutdown();server.server_close();thread.join()

    def test_assignment_epoch_ignores_comments_but_detects_return_to_same_assignee(self):
        item=self.create(assignee=self.a)
        path='/v1/projects/items/'+item['id']+'/assignment'
        first=self.api.dispatch(path,owner=True)
        self.assertEqual(first['assignment_revision'],item['revision'])
        item=self.update(item,title='Ordinary edit')
        self.assertEqual(self.api.dispatch(path,owner=True)['assignment_revision'],first['assignment_revision'])
        item=self.update(item,assignee=self.b)
        item=self.update(item,assignee=self.a)
        self.assertGreater(self.api.dispatch(path,owner=True)['assignment_revision'],first['assignment_revision'])
        item=self.update(item,deleted=True)
        self.assertTrue(self.api.dispatch(path,owner=True)['deleted'])

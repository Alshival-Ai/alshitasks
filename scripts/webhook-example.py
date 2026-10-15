#!/usr/bin/env python3
"""Minimal non-agent receiver. Set WEBHOOK_SECRET and run on loopback.
This example verifies signatures and prints IDs; production receivers must persist
receipts and deduplicate by source.instance_id + event.id before doing work.
"""
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
import time

secret = os.environ['WEBHOOK_SECRET'].encode()
class Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536 or self.headers.get('Transfer-Encoding'):
                raise ValueError()
            stamp = self.headers['X-AlshiTasks-Timestamp']
            if abs(time.time()-int(stamp)) > 300:
                raise ValueError()
            raw = self.rfile.read(size)
            expected = 'sha256='+hmac.new(secret, stamp.encode()+b'.'+raw, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected,self.headers.get('X-AlshiTasks-Signature','')):
                raise ValueError()
            event=json.loads(raw)
            print(json.dumps({'event':event['type'],'id':event['id']}),flush=True)
            self.send_response(202)
        except (ValueError,TypeError,KeyError):
            self.send_response(400)
        self.end_headers()
    def log_message(self,*args): pass
HTTPServer(('127.0.0.1',43122),Receiver).serve_forever()

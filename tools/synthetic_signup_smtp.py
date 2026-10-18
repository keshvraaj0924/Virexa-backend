"""Loopback-only disposable SMTP sink for synthetic browser tests. Never use real emails.
Usage: python tools/synthetic_signup_smtp.py PORT PRIVATE_CAPTURE_DIRECTORY
Captures keyed by SHA256(lowercase recipient). Do not attach captures to test artifacts.
"""
import hashlib
import json
import os
import socketserver
import sys
from email import policy
from email.parser import BytesParser
from pathlib import Path

capture = Path(sys.argv[2])
capture.mkdir(mode=0o700, parents=True, exist_ok=True)
os.chmod(capture, 0o700)


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(10)
        self.wfile.write(b"220 synthetic.local ESMTP\r\n")
        while line := self.rfile.readline(4096):
            command = line.split(b" ", 1)[0].strip().upper()
            if command == b"DATA":
                self.wfile.write(b"354 End with dot\r\n")
                chunks = []
                size = 0
                while (part := self.rfile.readline(4096)) != b".\r\n":
                    size += len(part)
                    if not part or size > 16384:
                        return
                    chunks.append(part)
                message = BytesParser(policy=policy.default).parsebytes(b"".join(chunks))
                recipient = str(message["To"]).lower()
                if not recipient.endswith(("@example.com", "@example.invalid", "@example.test")):
                    self.wfile.write(b"550 Synthetic addresses only\r\n")
                    continue
                payload = {"email": recipient, "body": message.get_content()}
                path = capture / (hashlib.sha256(recipient.encode()).hexdigest() + ".json")
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "w") as output:
                    json.dump(payload, output)
                self.wfile.write(b"250 Captured locally\r\n")
            elif command in {b"EHLO", b"HELO", b"MAIL", b"RCPT", b"RSET", b"NOOP"}:
                self.wfile.write(b"250 OK\r\n")
            elif command == b"QUIT":
                self.wfile.write(b"221 Bye\r\n")
                return
            else:
                self.wfile.write(b"500 Unsupported\r\n")


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True


with Server(("127.0.0.1", int(sys.argv[1])), Handler) as server:
    server.serve_forever()

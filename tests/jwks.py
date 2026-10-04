"""A real OIDC issuer for tests: one RSA key, published over loopback HTTP.

Real HTTP on purpose: the verifier under test fetches `jwks_uri` itself, and a
mocked fetch would test the mock (memory: test the property, not the helper).
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from fastmcp.server.auth.providers.jwt import RSAKeyPair
from joserfc.jwk import RSAKey

from kubed.selenium_flow.config import Settings

KID = "test-key"
AUDIENCE = "https://mcp.example.com"


class Issuer:
    def __init__(self):
        self.keys = RSAKeyPair.generate()
        jwk = RSAKey.import_key(self.keys.public_key).as_dict(private=False)
        body = json.dumps({"keys": [jwk | {"kid": KID, "use": "sig", "alg": "RS256"}]})

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # the stdlib's name
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())

            def log_message(self, *args):
                pass

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.http.serve_forever, daemon=True).start()
        port = self.http.server_address[1]
        self.issuer = f"http://127.0.0.1:{port}/realms/example"
        self.jwks_uri = f"{self.issuer}/protocol/openid-connect/certs"

    def mint(self, *, keys: RSAKeyPair | None = None, **overrides) -> str:
        claims = {"preferred_username": "drk", "roles": ["mcp"]}
        claims |= overrides.pop("claims", {})
        return (keys or self.keys).create_token(
            subject=overrides.pop("subject", "6b0f"),
            issuer=overrides.pop("issuer", self.issuer),
            audience=overrides.pop("audience", AUDIENCE),
            expires_in_seconds=overrides.pop("expires_in_seconds", 300),
            additional_claims=claims,
            kid=KID,
        )

    def settings(self, token: str, roles=("mcp",)) -> Settings:
        return Settings(
            grid={"url": "http://grid.invalid:4444"},
            auth={"token": token},
            oidc={
                "issuer": self.issuer,
                "audience": AUDIENCE,
                "jwks_uri": self.jwks_uri,
                "roles": list(roles),
            },
        )

    def close(self):
        self.http.shutdown()

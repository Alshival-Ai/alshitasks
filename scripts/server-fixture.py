"""Create ONLY a new isolated instance for automated server acceptance tests."""
import json
import secrets
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'service'))
from alshitasks.paths import Paths
from alshitasks.server_auth import Auth
from alshitasks.server_secrets import Vault
from alshitasks.platform_support import protect
from cryptography.fernet import Fernet

root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=False)
key = root / 'master.key'
key.write_bytes(Fernet.generate_key())
protect(key)
instance = root / 'instance'
Vault(instance, key)
password = secrets.token_urlsafe(32)
Auth(Paths(instance)).password(password)
metadata = root / 'fixture.json'
metadata.write_text(json.dumps({'password': password, 'root': str(instance), 'key': str(key)}))
protect(metadata)
# Credentials remain in the restricted fixture file; never log them.
print('Isolated server fixture initialized')
if '--tls' in sys.argv:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import hashes, serialization
    from datetime import datetime, timedelta, timezone
    import ipaddress
    tls = root / 'tls'
    tls.mkdir()
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Alshitask isolated TLS test')])
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(private.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc)-timedelta(minutes=5))
        .not_valid_after(datetime.now(timezone.utc)+timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True)
        .sign(private,hashes.SHA256()))
    (tls/'cert.pem').write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    (tls/'key.pem').write_bytes(private.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    protect(tls/'key.pem')

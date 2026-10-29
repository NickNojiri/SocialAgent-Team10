"""Write the Windows trust store (which includes this network's TLS-inspection root) to a
PEM file, so tools that don't use the OS store (pyarrow's S3 client, DuckDB) can verify
HTTPS here. Prints the path; set SSL_CERT_FILE / AWS_CA_BUNDLE / CURL_CA_BUNDLE to it."""
import ssl
from pathlib import Path

out = Path(__file__).resolve().parents[2] / "data" / "experiments" / "windows_roots.pem"
ctx = ssl.create_default_context()
ctx.load_default_certs()
pems = [ssl.DER_cert_to_PEM_cert(der) for der in ctx.get_ca_certs(binary_form=True)]
try:
    import certifi
    pems.append(Path(certifi.where()).read_text(encoding="utf-8"))
except ImportError:
    pass
out.write_text("".join(pems), encoding="utf-8", newline="\n")
print(out)

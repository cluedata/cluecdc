import ssl

from scripts.kafka_security_acceptance import certificates


def test_acceptance_certificates_pass_strict_verified_tls_handshake(tmp_path):
    """SEC-03/OPS-03: fixtures must pass strict CA/hostname checks without TLS exceptions."""
    certificates(tmp_path, "fixture.example.com")
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(tmp_path / "server.pem")
    client_context = ssl.create_default_context(cafile=str(tmp_path / "ca.pem"))
    client_context.verify_flags |= ssl.VERIFY_X509_STRICT
    client_in, client_out, server_in, server_out = (ssl.MemoryBIO() for _ in range(4))
    client = client_context.wrap_bio(client_in, client_out, server_hostname="fixture.example.com")
    server = server_context.wrap_bio(server_in, server_out, server_side=True)
    complete = set()
    for _ in range(10):
        for name, endpoint in (("client", client), ("server", server)):
            try:
                endpoint.do_handshake()
                complete.add(name)
            except ssl.SSLWantReadError:
                pass
        if data := client_out.read():
            server_in.write(data)
        if data := server_out.read():
            client_in.write(data)
        if len(complete) == 2:
            break
    assert complete == {"client", "server"}
    assert ("DNS", "fixture.example.com") in client.getpeercert()["subjectAltName"]

"""Host e schema con cui il client ha raggiunto il servizio.

Si usa solo l'header Host (cloudflared lo imposta all'hostname pubblico); X-Forwarded-Host
lo può falsificare chiunque e non viene letto. X-Forwarded-Proto serve per sapere se
dietro il tunnel la richiesta era HTTPS.
"""
import ipaddress

from starlette.requests import Request

# RFC 1918: le uniche reti "private" in senso stretto (non le altre riservate di
# ipaddress.is_private, che includono anche i blocchi di documentazione RFC 5737 come
# 203.0.113.0/24, usati apposta nei test per simulare un client da Internet).
_PRIVATE_NETWORKS = tuple(ipaddress.ip_network(net) for net in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
))


def _host_header(request: Request) -> str:
    return request.headers.get("host", "").strip()


def public_host(request: Request) -> str:
    return _host_header(request).split(":")[0].lower()


def public_scheme(request: Request) -> str:
    raw = request.headers.get("x-forwarded-proto")
    if raw:
        return raw.split(",")[0].strip().lower()
    return request.url.scheme


def public_base_url(request: Request) -> str:
    return f"{public_scheme(request)}://{_host_header(request)}"


def is_lan_request(request: Request) -> bool:
    """Richiesta dalla rete locale: IP privato o loopback e non passata dal tunnel.

    Cloudflare aggiunge sempre Cf-Connecting-Ip (sovrascrivendo quello del client), e
    le richieste del tunnel arrivano da cloudflared, che ha anch'esso un IP privato:
    è l'header, non l'IP, a distinguerle.
    """
    if "cf-connecting-ip" in request.headers or request.client is None:
        return False
    try:
        ip = ipaddress.ip_address(request.client.host)
    except ValueError:
        return False
    if ip.version == 6 and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_loopback:
        return True
    return isinstance(ip, ipaddress.IPv4Address) and any(ip in net for net in _PRIVATE_NETWORKS)

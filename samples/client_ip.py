# Excerpt from Rasova (private repository): core/utils.py (the client IP part)
# Shown as written in production. It imports from the rest of the codebase,
# so it is for reading, not for running on its own.

import ipaddress
import logging
from functools import lru_cache

from django.conf import settings

logger = logging.getLogger("pos.core")


@lru_cache(maxsize=None)
def _networks(cidrs):
    return tuple(ipaddress.ip_network(c.strip(), strict=False) for c in cidrs if c.strip())


def _valid_ip(value):
    try:
        ipaddress.ip_address(value)
        return True
    except ValueError:
        return False


def ip_in(ip, cidrs):
    """True when `ip` is a valid address inside any of `cidrs` (plain IPs count as /32)."""
    try:
        address = ipaddress.ip_address(str(ip).strip())
    except ValueError:
        return False
    return any(address in network for network in _networks(tuple(cidrs)))


_warned_missing_real_ip = False


def get_client_ip(request):
    """
    The real visitor's IP behind Cloudflare -> nginx -> gunicorn.

    Every header a request carries can be written by whoever sends it, so a
    header is only believed when the machine that handed it over is one we
    trust to have set it:

    1. gunicorn listens on 127.0.0.1, so REMOTE_ADDR is nginx. nginx sets
       X-Real-IP to the address of whoever connected to it, replacing any
       X-Real-IP the client sent (`proxy_set_header`, nginx_rasova.conf).
       So X-Real-IP is believed only when REMOTE_ADDR is a trusted proxy.
    2. CF-Connecting-IP is believed only when that connecting address is
       one of Cloudflare's own (settings.CLOUDFLARE_IP_RANGES). Anyone who
       reaches the server directly, around Cloudflare, can send the header,
       and used to be taken at their word: any IP they liked, including
       127.0.0.1, the aggregator allowlist's default.
    3. X-Forwarded-For is never believed: nginx appends to whatever the
       client sent, so its first hop is the client's own claim.

    Rate limits, login lockouts (axes) and the aggregator allowlist all ask
    this one function.
    """
    global _warned_missing_real_ip
    meta = request.META
    peer = str(meta.get("REMOTE_ADDR", "") or "").strip()

    if ip_in(peer, settings.TRUSTED_PROXY_IPS):
        real_ip = str(meta.get("HTTP_X_REAL_IP", "") or "").strip()
        if _valid_ip(real_ip):
            peer = real_ip
        elif meta.get("HTTP_CF_CONNECTING_IP") and not _warned_missing_real_ip:
            # Cloudflare traffic arriving from nginx without X-Real-IP means
            # nginx is not configured as in nginx_rasova.conf: every visitor
            # would share one IP for rate limits and lockouts.
            _warned_missing_real_ip = True
            logger.warning(
                "Proxied request has CF-Connecting-IP but no X-Real-IP: "
                "check nginx sets `proxy_set_header X-Real-IP $remote_addr`."
            )

    if ip_in(peer, settings.CLOUDFLARE_IP_RANGES):
        visitor = str(meta.get("HTTP_CF_CONNECTING_IP", "") or "").strip()
        if _valid_ip(visitor):
            return visitor
    return peer


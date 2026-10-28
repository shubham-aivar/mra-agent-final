"""The Content-Security-Policy of the hosted page. One definition, used by the CDK stack (CloudFront adds it to every response) and by the test
harness, so the browser tests run under exactly the policy production uses."""
from __future__ import annotations

from collections.abc import Iterable


def hosted_csp(region: str, data_bucket_host: str, extra_connect: Iterable[str] = ()) -> str:
    """Scripts and styles only from the site itself (no inline code). The page may call: itself (the API), Cognito (sign-in), and the data bucket
    (direct uploads of files). It cannot be framed, and it loads nothing from anywhere else."""
    connect = ["'self'", f"https://cognito-idp.{region}.amazonaws.com", f"https://{data_bucket_host}", *extra_connect]
    return "; ".join(["default-src 'none'", "script-src 'self'", "style-src 'self'", "img-src 'self' data:", f"connect-src {' '.join(connect)}",
                      "base-uri 'none'", "form-action 'none'", "frame-ancestors 'none'"])

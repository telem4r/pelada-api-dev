"""Read-only preflight. Never pass tokens/secrets as command-line arguments.

Run from api/: python -m scripts.check_supabase_auth --verify-token-stdin
Without a token: --algorithm HS256|ES256|RS256 only checks key availability.
"""
from __future__ import annotations

import argparse
import getpass
import json
import sys

from jose import JWTError, jwk, jwt
from jose.exceptions import JWKError

from app.core import supabase_auth as auth


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check JWT verification without displaying secrets or user data")
    parser.add_argument("--algorithm", choices=["HS256", "ES256", "RS256"])
    parser.add_argument("--verify-token-stdin", action="store_true", help="Read one access token from stdin; never echo it")
    args = parser.parse_args(argv)
    if not args.verify_token_stdin and not args.algorithm:
        parser.error("choose --algorithm or --verify-token-stdin")
    try:
        if args.verify_token_stdin:
            token = (getpass.getpass("Access token (hidden): ") if sys.stdin.isatty()
                     else sys.stdin.readline(65537)).strip()
            if not token or len(token) > 65536:
                raise auth.SupabaseAuthError("Invalid input")
            # This header is descriptive only. Authentication is performed by
            # _decode_token with the same verifier used by the actual API.
            algorithm = jwt.get_unverified_header(token).get("alg")
            if args.algorithm and algorithm != args.algorithm:
                raise auth.SupabaseAuthError("Unexpected algorithm")
            auth._decode_token(token)
            result = {"status": "token_verified", "algorithm": algorithm}
        else:
            if not auth.settings.supabase_url or not auth.settings.supabase_jwt_audience:
                raise auth.SupabaseAuthUnavailable("Missing configuration")
            if args.algorithm == "HS256":
                available = bool(auth._get_jwt_secret())
            else:
                keys = auth.get_jwks()
                if keys is None:
                    raise auth.SupabaseAuthUnavailable("Keys unavailable")
                available = False
                for key in keys["keys"]:
                    kid = key.get("kid")
                    if not isinstance(kid, str) or not kid:
                        continue
                    selected = auth._select_public_key(keys, kid=kid, alg=args.algorithm)
                    if selected is not None:
                        jwk.construct(selected, args.algorithm)
                        available = True
            if not available:
                raise auth.SupabaseAuthUnavailable("Verification material unavailable")
            result = {"status": "key_material_available", "algorithm": args.algorithm,
                      "note": "This does not validate a user session or prove a shared secret is correct"}
        print(json.dumps(result))
        return 0
    except (auth.SupabaseAuthUnavailable, JWKError):
        print(json.dumps({"status": "verification_unavailable"}))
        return 2
    except (auth.SupabaseAuthError, JWTError, ValueError, TypeError, OverflowError, AttributeError):
        print(json.dumps({"status": "token_or_key_invalid"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Mint a DEV JWT for the widget/demo. In production Grovator's own auth issues tokens.
Example: python -m scripts.mint_token --role teacher --user t1 --batches b1"""
import argparse
import time

import jwt

from app.config import get_settings

ap = argparse.ArgumentParser()
ap.add_argument("--role", default="student", choices=["student", "teacher", "admin", "superadmin"])
ap.add_argument("--user", default="s1")
ap.add_argument("--org", default="org1")
ap.add_argument("--school", default="sch1")
ap.add_argument("--batches", default="b1", help="comma separated")
ap.add_argument("--hours", type=int, default=12)
a = ap.parse_args()
s = get_settings()
claims = {"sub": a.user, "role": a.role, "org_id": a.org, "school_id": a.school or None,
          "batch_ids": [b for b in a.batches.split(",") if b], "exp": int(time.time()) + a.hours * 3600}
print(jwt.encode(claims, s.jwt_secret, algorithm=s.jwt_algorithm))

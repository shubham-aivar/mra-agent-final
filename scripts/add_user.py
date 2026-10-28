"""Create (or reset) a sign-in for the hosted page: a username and a password, no MFA.

    python3 scripts/add_user.py --username mra-analyst                 # asks for the password; they must choose a new one at first sign-in
    python3 scripts/add_user.py --username mra-analyst --permanent           # the password you type is the one they keep (handy for a demo account)
    python3 scripts/add_user.py --username viewer --reader            # can look at everything, change nothing
    python3 scripts/add_user.py --username mra-analyst --reset --permanent   # set a new password for an existing user

The user pool is found from the deployed stack (--stack, default MraAgent). Uses your AWS credentials (set AWS_PROFILE or pass --profile).
The password is read from a hidden prompt (or MRA_NEW_PASSWORD) and is never printed.
"""
from __future__ import annotations

import argparse
import getpass
import os
import re
import sys

USERNAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._@+-]{0,127}$")


def password_problems(pw: str) -> list[str]:
    out = []
    if len(pw) < 10:
        out.append("at least 10 characters")
    if not re.search(r"[a-z]", pw):
        out.append("a small letter")
    if not re.search(r"[A-Z]", pw):
        out.append("a capital letter")
    if not re.search(r"\d", pw):
        out.append("a digit")
    return out


def add_user(idp, pool_id: str, username: str, password: str, group: str | None, permanent: bool, reset: bool = False,
             email: str | None = None) -> str:
    """Returns a one-line summary. Raises ValueError for anything a person should fix."""
    if not USERNAME.match(username):
        raise ValueError("the username may use letters, digits and . _ @ + - (1 to 128 characters, starting with a letter or digit)")
    missing = password_problems(password)
    if missing:
        raise ValueError("the password needs " + ", ".join(missing))
    exists = True
    try:
        idp.admin_get_user(UserPoolId=pool_id, Username=username)
    except idp.exceptions.UserNotFoundException:
        exists = False
    if exists and not reset:
        raise ValueError(f"'{username}' already exists: add --reset to set a new password")
    if not exists:
        attrs = [{"Name": "email", "Value": email}, {"Name": "email_verified", "Value": "true"}] if email else []
        idp.admin_create_user(UserPoolId=pool_id, Username=username, TemporaryPassword=password, MessageAction="SUPPRESS", UserAttributes=attrs)
    if permanent or exists:                          # an existing user's reset is always set directly; a new user's is only permanent if asked
        idp.admin_set_user_password(UserPoolId=pool_id, Username=username, Password=password, Permanent=permanent)
    if group:
        idp.admin_add_user_to_group(UserPoolId=pool_id, Username=username, GroupName=group)
    role = f"in the '{group}' group (can change things)" if group else "read only (no group)"
    first = "keeps this password" if permanent else "must choose a new password at the first sign-in"
    return f"{'Reset' if exists else 'Created'} '{username}': {role}; {first}."


def stack_outputs(session, stack: str) -> dict:
    cf = session.client("cloudformation")
    try:
        outs = cf.describe_stacks(StackName=stack)["Stacks"][0].get("Outputs", [])
    except Exception as e:
        raise ValueError(f"could not read the stack '{stack}': {e}") from e
    return {o["OutputKey"]: o["OutputValue"] for o in outs}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--username", required=True)
    ap.add_argument("--email")
    ap.add_argument("--reader", action="store_true", help="no reviewer group: can look, cannot change")
    ap.add_argument("--permanent", action="store_true", help="the password is final (no change at first sign-in)")
    ap.add_argument("--reset", action="store_true", help="the user exists: set a new password")
    ap.add_argument("--stack", default="MraAgent")
    ap.add_argument("--user-pool-id", help="skip reading the stack")
    ap.add_argument("--group", help="the reviewer group (default: from the stack)")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")
    ap.add_argument("--profile")
    a = ap.parse_args(argv)
    import boto3
    session = boto3.Session(profile_name=a.profile, region_name=a.region)
    try:
        pool, group = a.user_pool_id, a.group
        if not pool or (not group and not a.reader):
            outs = stack_outputs(session, a.stack)
            pool, group = pool or outs.get("UserPoolId"), group or outs.get("ReviewerGroup", "mra-reviewers")
        pw = os.environ.get("MRA_NEW_PASSWORD") or getpass.getpass("Password (hidden): ")
        if not os.environ.get("MRA_NEW_PASSWORD") and getpass.getpass("Repeat it: ") != pw:
            raise ValueError("the two passwords are not the same")
        print(add_user(session.client("cognito-idp"), pool, a.username, pw, None if a.reader else group, a.permanent, a.reset, a.email))
    except ValueError as e:
        print("Not done:", e)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())

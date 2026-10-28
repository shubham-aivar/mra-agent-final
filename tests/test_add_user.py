import boto3
import pytest
from moto import mock_aws

from scripts import add_user as au


@pytest.fixture
def pool(monkeypatch):
    for k, v in (("AWS_ACCESS_KEY_ID", "x"), ("AWS_SECRET_ACCESS_KEY", "y"), ("AWS_DEFAULT_REGION", "us-east-1")):
        monkeypatch.setenv(k, v)
    with mock_aws():
        idp = boto3.client("cognito-idp", region_name="us-east-1")
        pid = idp.create_user_pool(PoolName="p")["UserPool"]["Id"]
        idp.create_group(GroupName="mra-reviewers", UserPoolId=pid)
        idp.create_user_pool_client(UserPoolId=pid, ClientName="c", ExplicitAuthFlows=["ALLOW_USER_PASSWORD_AUTH"])
        yield idp, pid


def groups(idp, pid, user):
    return [g["GroupName"] for g in idp.admin_list_groups_for_user(UserPoolId=pid, Username=user)["Groups"]]


def test_a_reviewer_is_created_in_the_group_and_must_change_the_password_at_first_sign_in(pool):
    idp, pid = pool
    msg = au.add_user(idp, pid, "demo", "Demo#Pass12345", "mra-reviewers", permanent=False)
    assert "must choose a new password" in msg and "Demo#Pass12345" not in msg
    assert groups(idp, pid, "demo") == ["mra-reviewers"]
    assert idp.admin_get_user(UserPoolId=pid, Username="demo")["UserStatus"] == "FORCE_CHANGE_PASSWORD"


def test_a_permanent_password_is_final(pool):
    idp, pid = pool
    assert "keeps this password" in au.add_user(idp, pid, "demo", "Demo#Pass12345", "mra-reviewers", permanent=True)
    assert idp.admin_get_user(UserPoolId=pid, Username="demo")["UserStatus"] == "CONFIRMED"


def test_a_reader_has_no_group(pool):
    idp, pid = pool
    assert "read only" in au.add_user(idp, pid, "viewer", "Viewer#Pass123", None, permanent=True)
    assert groups(idp, pid, "viewer") == []


def test_an_email_can_be_attached_and_is_marked_verified(pool):
    idp, pid = pool
    au.add_user(idp, pid, "demo", "Demo#Pass12345", None, True, email="demo@example.com")
    attrs = {a["Name"]: a["Value"] for a in idp.admin_get_user(UserPoolId=pid, Username="demo")["UserAttributes"]}
    assert attrs["email"] == "demo@example.com" and attrs["email_verified"] == "true"


def test_an_existing_user_is_never_changed_without_reset(pool):
    idp, pid = pool
    au.add_user(idp, pid, "demo", "Demo#Pass12345", "mra-reviewers", True)
    with pytest.raises(ValueError, match="already exists"):
        au.add_user(idp, pid, "demo", "Other#Pass12345", "mra-reviewers", True)
    assert "Reset" in au.add_user(idp, pid, "demo", "Other#Pass12345", "mra-reviewers", True, reset=True)


@pytest.mark.parametrize("password,needle", [("short1A", "at least 10"), ("alllowercase123", "capital"), ("ALLUPPERCASE123", "small"), ("NoDigitsHereAtAll", "digit")])
def test_weak_passwords_are_refused_before_anything_is_created(pool, password, needle):
    idp, pid = pool
    with pytest.raises(ValueError, match=needle):
        au.add_user(idp, pid, "demo", password, None, True)
    assert idp.list_users(UserPoolId=pid)["Users"] == []


@pytest.mark.parametrize("name", ["", "-bad", "has space", "a/b", "x" * 200])
def test_bad_usernames_are_refused(pool, name):
    idp, pid = pool
    with pytest.raises(ValueError, match="username"):
        au.add_user(idp, pid, name, "Demo#Pass12345", None, True)


def test_the_command_line_reads_the_pool_from_the_stack_and_never_prints_the_password(pool, monkeypatch, capsys):
    idp, pid = pool
    monkeypatch.setenv("MRA_NEW_PASSWORD", "Demo#Pass12345")
    code = au.main(["--username", "demo", "--permanent", "--user-pool-id", pid, "--group", "mra-reviewers", "--region", "us-east-1"])
    out = capsys.readouterr().out
    assert code == 0 and "Created 'demo'" in out and "Demo#Pass12345" not in out and groups(idp, pid, "demo") == ["mra-reviewers"]
    assert au.main(["--username", "demo", "--user-pool-id", pid, "--group", "mra-reviewers", "--region", "us-east-1"]) == 2
    assert "already exists" in capsys.readouterr().out


def test_a_missing_stack_is_reported_plainly(pool, monkeypatch, capsys):
    monkeypatch.setenv("MRA_NEW_PASSWORD", "Demo#Pass12345")
    assert au.main(["--username", "demo", "--stack", "NoSuchStack", "--region", "us-east-1"]) == 2
    assert "could not read the stack" in capsys.readouterr().out

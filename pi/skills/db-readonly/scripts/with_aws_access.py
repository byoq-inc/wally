#!/usr/bin/env python3
"""Run a command with AWS_access fields loaded from 1Password.

Secrets are placed in environment variables for the child process and are never
printed by this script.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_VAULT = os.environ.get("AWS_ACCESS_OP_VAULT", "Automation")
DEFAULT_ITEM = os.environ.get("AWS_ACCESS_OP_ITEM", "AWS_access")

FIELD_ENV = {
    "username": "AWS_ACCESS_USERNAME",
    "hostname": "AWS_ACCESS_HOSTNAME",
    "credential": "AWS_ACCESS_CREDENTIAL",
    "type": "AWS_ACCESS_TYPE",
    "filename": "AWS_ACCESS_FILENAME",
    "valid from": "AWS_ACCESS_VALID_FROM",
    "expires": "AWS_ACCESS_EXPIRES",
}


def op_item(vault: str, item: str) -> dict:
    proc = subprocess.run(
        ["op", "item", "get", item, "--vault", vault, "--format", "json"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return json.loads(proc.stdout)


def fields_by_label(item: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    for field in item.get("fields") or []:
        label = str(field.get("label") or field.get("id") or "").strip()
        value = field.get("value")
        if label and value is not None:
            out[label.lower()] = str(value)
    return out


def build_env(fields: dict[str, str], args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    for label, env_name in FIELD_ENV.items():
        if label in fields:
            env[env_name] = fields[label]

    # AWS_access conventions in 1Password vary. Common labels are:
    # - username: AWS access key id
    # - credential/password/secret access key: AWS secret access key
    # - session token/security token: AWS session token
    # Do not print any of these values.
    access_key = first_present(fields, ["aws_access_key_id", "access key id", "access_key_id", "username"])
    secret_key = first_present(fields, ["aws_secret_access_key", "secret access key", "secret_access_key", "password"])
    session_token = first_present(fields, ["aws_session_token", "session token", "security token", "session_token"])

    credential = fields.get("credential")
    if credential and not secret_key and not session_token:
        # AWS secret access keys are typically 40 chars. STS session tokens are much longer.
        if len(credential) <= 128:
            secret_key = credential
        else:
            session_token = credential

    if access_key:
        env["AWS_ACCESS_KEY_ID"] = access_key
    if secret_key:
        env["AWS_SECRET_ACCESS_KEY"] = secret_key
    if session_token:
        env["AWS_SESSION_TOKEN"] = session_token
    if args.region:
        env["AWS_DEFAULT_REGION"] = args.region

    if args.validate_aws and not (env.get("AWS_ACCESS_KEY_ID") and env.get("AWS_SECRET_ACCESS_KEY")):
        missing = []
        if not env.get("AWS_ACCESS_KEY_ID"):
            missing.append("AWS_ACCESS_KEY_ID")
        if not env.get("AWS_SECRET_ACCESS_KEY"):
            missing.append("AWS_SECRET_ACCESS_KEY")
        raise SystemExit(f"AWS_access is missing required AWS credential component(s): {', '.join(missing)}")

    if args.pg:
        if "hostname" in fields:
            env["PGHOST"] = fields["hostname"]
        if "username" in fields:
            env["PGUSER"] = fields["username"]
        if "credential" in fields:
            env["PGPASSWORD"] = fields["credential"]

    if args.mysql and "credential" in fields:
        env["MYSQL_PWD"] = fields["credential"]

    return env


def first_present(fields: dict[str, str], labels: list[str]) -> str | None:
    for label in labels:
        value = fields.get(label)
        if value:
            return value
    return None


def materialize_key(env: dict[str, str]) -> tuple[dict[str, str], str | None]:
    credential = env.get("AWS_ACCESS_CREDENTIAL", "")
    if not credential:
        raise SystemExit("AWS_access credential field is empty; cannot create key file")
    fd, path = tempfile.mkstemp(prefix="aws-access-", suffix=".key")
    os.close(fd)
    key_path = Path(path)
    key_path.write_text(credential)
    key_path.chmod(0o600)
    next_env = env.copy()
    next_env["AWS_ACCESS_KEY_FILE"] = str(key_path)
    return next_env, str(key_path)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Run a command with 1Password AWS_access fields in env")
    parser.add_argument("--vault", default=DEFAULT_VAULT)
    parser.add_argument("--item", default=DEFAULT_ITEM)
    parser.add_argument("--pg", action="store_true", help="Also map hostname/username/credential to PGHOST/PGUSER/PGPASSWORD")
    parser.add_argument("--mysql", action="store_true", help="Also map credential to MYSQL_PWD")
    parser.add_argument("--ssh-key-file", action="store_true", help="Write credential to a temporary 0600 key file and set AWS_ACCESS_KEY_FILE")
    parser.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION"), help="Set AWS_DEFAULT_REGION for the child command")
    parser.add_argument("--validate-aws", action="store_true", help="Fail before running command if AWS access key id or secret access key is missing")
    parser.add_argument("--print-nonsecret", action="store_true", help="Print loaded non-secret field names only; values are redacted")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to exec after --")
    args = parser.parse_args(argv)

    command = args.command
    if command and command[0] == "--":
        command = command[1:]

    item = op_item(args.vault, args.item)
    fields = fields_by_label(item)
    env = build_env(fields, args)
    key_file: str | None = None

    if args.ssh_key_file:
        env, key_file = materialize_key(env)

    if args.print_nonsecret:
        present_fields = sorted(k for k in fields if k != "notesplain")
        present_env = sorted(set(
            [k for k in env if k.startswith("AWS_ACCESS_") and k != "AWS_ACCESS_CREDENTIAL"]
            + [k for k in ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_DEFAULT_REGION"] if env.get(k)]
        ))
        print(json.dumps({"vault": args.vault, "item": args.item, "field_labels_present": present_fields, "env_present": present_env}, indent=2))

    if not command:
        if key_file:
            Path(key_file).unlink(missing_ok=True)
        return 0

    try:
        proc = subprocess.run(command, env=env)
        return proc.returncode
    finally:
        if key_file:
            Path(key_file).unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

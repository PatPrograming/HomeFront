"""The staff directory: Cognito users and their group (`staffer` or `admin`), read with the server's own AWS
credentials. Only the admin "People & roles" routes use it. Tests swap in a fake with `set_directory`.

IAM the server needs on the user pool: cognito-idp:ListUsers, ListUsersInGroup, AdminAddUserToGroup,
AdminRemoveUserFromGroup.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import get_settings

GROUPS = ("staffer", "admin")


@dataclass(frozen=True)
class DirectoryUser:
    username: str
    email: str | None
    enabled: bool
    group: str | None  # "admin" wins if a user is somehow in both; None when in neither


class Directory(Protocol):
    def list_users(self) -> list[DirectoryUser]: ...

    def set_group(self, username: str, group: str) -> None: ...


class CognitoDirectory:
    def __init__(self, client: Any, pool_id: str) -> None:
        self.client = client
        self.pool_id = pool_id

    @classmethod
    def from_settings(cls) -> "CognitoDirectory":
        import boto3

        settings = get_settings()
        if not settings.cognito_user_pool_id:
            raise RuntimeError("COGNITO_USER_POOL_ID is not set")
        return cls(boto3.client("cognito-idp", region_name=settings.cognito_region), settings.cognito_user_pool_id)

    def _members(self, group: str) -> set[str]:
        pages = self.client.get_paginator("list_users_in_group").paginate(UserPoolId=self.pool_id, GroupName=group)
        return {u["Username"] for page in pages for u in page["Users"]}

    def list_users(self) -> list[DirectoryUser]:
        members = {g: self._members(g) for g in GROUPS}
        users = []
        for page in self.client.get_paginator("list_users").paginate(UserPoolId=self.pool_id):
            for u in page["Users"]:
                name = u["Username"]
                email = next((a["Value"] for a in u.get("Attributes", []) if a["Name"] == "email"), None)
                group = "admin" if name in members["admin"] else "staffer" if name in members["staffer"] else None
                users.append(DirectoryUser(name, email, u.get("Enabled", True), group))
        return sorted(users, key=lambda u: u.username)

    def set_group(self, username: str, group: str) -> None:
        """Put the user in `group` and take them out of the other staff group (add first, so they are never
        in neither)."""
        self.client.admin_add_user_to_group(UserPoolId=self.pool_id, Username=username, GroupName=group)
        for other in GROUPS:
            if other != group:
                self.client.admin_remove_user_from_group(UserPoolId=self.pool_id, Username=username, GroupName=other)


_directory: Directory | None = None


def get_directory() -> Directory:
    global _directory
    if _directory is None:
        _directory = CognitoDirectory.from_settings()
    return _directory


def set_directory(directory: Directory | None) -> None:
    global _directory
    _directory = directory

"""账号资料通过有效会话更新；修改密码额外核验当前密码。"""

from app.auth.security import DUMMY_PASSWORD_HASH, hash_password, verify_password
from app.auth.store import AuthStore, SessionChanged


class IncorrectPassword(Exception):
    pass


class SamePassword(Exception):
    pass


class AccountService:
    def __init__(self, store: AuthStore):
        self.store = store

    def authenticate(self, user_id: int, ip: str, current_password: str):
        self.store.reserve_account_attempt(user_id, ip)
        row = self.store.find_user_by_id(user_id)
        valid = verify_password(current_password, row["password_hash"] if row else DUMMY_PASSWORD_HASH)
        if row is None:
            raise SessionChanged
        if not valid:
            raise IncorrectPassword
        return row

    def rename(self, user_id: int, token: str, ip: str, username: str):
        self.store.reserve_account_attempt(user_id, ip)
        row = self.store.find_user_by_id(user_id)
        if row is None:
            raise SessionChanged
        # 写入时仍在事务内复核会话与账号版本，不能凭用户名修改他人资料。
        return self.store.rename_account(user_id, row["auth_version"], token, username)

    def change_password(self, user_id: int, token: str, ip: str, current_password: str, new_password: str, session_max_age: int):
        row = self.authenticate(user_id, ip, current_password)
        if current_password == new_password:
            raise SamePassword
        return self.store.change_account_password(user_id, row["auth_version"], token, hash_password(new_password), session_max_age)

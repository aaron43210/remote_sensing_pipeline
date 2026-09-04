# =============================================================
# OWNER: AARON
# =============================================================
class AuthHandler:
    def verify_token(self):
        return {"user": "admin"}
auth_handler = AuthHandler()

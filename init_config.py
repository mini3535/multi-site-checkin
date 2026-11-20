"""Print a new configuration without importing or starting the application."""

import base64
import secrets


def main():
    password = secrets.token_urlsafe(32)
    secret_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    print("# 此文件包含管理员密码和数据密钥，请保密并与数据一起备份。")
    print(f"AUTOSIGN_ADMIN_PASSWORD={password}")
    print(f"AUTOSIGN_SECRET_KEY={secret_key}")
    print("AUTOSIGN_ALLOW_PRIVATE_TARGETS=false")
    print("AUTOSIGN_COOKIE_SECURE=false")
    print("AUTOSIGN_TIMEZONE=Asia/Shanghai")
    print("BIND_ADDRESS=127.0.0.1")
    print("PORT=8080")


if __name__ == "__main__":
    main()

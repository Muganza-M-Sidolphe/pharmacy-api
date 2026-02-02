import secrets
import string


def generate_temp_password(length=12):
    """
    Generate a secure temporary password.
    """
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    password = ''.join(secrets.choice(alphabet) for _ in range(length))
    return password